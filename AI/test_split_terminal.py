import asyncio
from threading import Thread
from time import monotonic, sleep
from unittest import TestCase, IsolatedAsyncioTestCase, main
import sys
import logging

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from split_terminal import LogStream, SplitTerminal


class StreamTests(TestCase):
    def test_ui_thread_stops_and_restores_streams(self):
        with create_pipe_input() as pipe:
            commands=[]
            ui=SplitTerminal(commands.append,input=pipe,output=DummyOutput())
            original=(sys.stdout,sys.stderr)
            thread=Thread(target=ui.run,daemon=True)
            thread.start()
            try:
                deadline=monotonic()+2
                while not ui.app.is_running and monotonic()<deadline: sleep(.01)
                self.assertTrue(ui.app.is_running)
                print('background print captured')
                pipe.send_text('status\r')
                deadline=monotonic()+2
                while not commands and monotonic()<deadline: sleep(.01)
                self.assertEqual(commands,['status'])
                self.assertIn('background print captured',ui.stream.snapshot()[1])
            finally:
                ui.stop(); thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertIs(sys.stdout,original[0]); self.assertIs(sys.stderr,original[1])

    def test_bounded_threaded_logs_and_terminal_escape_removal(self):
        stream=LogStream(max_lines=10,max_width=100)
        workers=[Thread(target=lambda: [stream.write('\x1b[31mmessage\x1b[0m\n') for _ in range(50)]) for _ in range(3)]
        for thread in workers: thread.start()
        for thread in workers: thread.join()
        _,text=stream.snapshot()
        self.assertEqual(text.count('message'),10)
        self.assertNotIn('\x1b',text)
        stream.write('x'*10000)
        self.assertLessEqual(len(stream.partial),100)

    def test_streams_are_restored_when_ui_fails(self):
        # Run the context manager directly without opening a real terminal.
        ui=object.__new__(SplitTerminal); ui.stream=LogStream()
        out,err=sys.stdout,sys.stderr
        with self.assertRaises(RuntimeError):
            with ui.captured_streams():
                print('from print')
                sys.stderr.write('from logger\n')
                raise RuntimeError('test failure')
        self.assertIs(sys.stdout,out); self.assertIs(sys.stderr,err)
        self.assertIn('from print',ui.stream.snapshot()[1])
        self.assertIn('from logger',ui.stream.snapshot()[1])

    def test_existing_library_console_handlers_are_captured_and_restored(self):
        ui=object.__new__(SplitTerminal); ui.stream=LogStream()
        logger=logging.getLogger('split_terminal_library_test')
        handler=logging.StreamHandler(sys.stderr)
        logger.addHandler(handler); logger.setLevel(logging.INFO); logger.propagate=False
        original=handler.stream
        try:
            with ui.captured_streams():
                logger.info('library message')
                self.assertIs(handler.stream,ui.stream)
            self.assertIs(handler.stream,original)
            self.assertIn('library message',ui.stream.snapshot()[1])
        finally:
            logger.removeHandler(handler); handler.close()


class ResizableOutput(DummyOutput):
    rows=24
    def get_size(self): return Size(rows=self.rows,columns=100)


class InterfaceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context=create_pipe_input()
        self.pipe=self.context.__enter__()
        self.output=ResizableOutput()
        self.commands=[]
        self.ui=SplitTerminal(self.commands.append,input=self.pipe,output=self.output)
        self.task=asyncio.create_task(self.ui.app.run_async(handle_sigint=False))
        await self.settle()

    async def settle(self):
        await asyncio.sleep(.12)

    async def asyncTearDown(self):
        self.ui.stop()
        await asyncio.wait_for(self.task,2)
        self.context.__exit__(None,None,None)

    async def test_log_updates_and_scrolling_preserve_draft_and_input_focus(self):
        self.pipe.send_text('simulate_pump one 3000')
        await self.settle()
        for n in range(120): self.ui.stream.write(f'log message {n}\n')
        self.ui.app.invalidate(); await self.settle()
        self.assertEqual(self.ui.command.text,'simulate_pump one 3000')
        self.assertIn('log message 119',self.ui.log.text)
        self.assertIs(self.ui.app.layout.current_buffer,self.ui.command.buffer)
        self.assertEqual(self.ui.command.window.render_info.window_height,1)
        self.assertGreater(self.ui.log.window.render_info.window_height,10)
        self.pipe.send_text('\x1b[5~')  # Page Up.
        await self.settle()
        self.assertFalse(self.ui.follow)
        self.assertEqual(self.ui.command.text,'simulate_pump one 3000')
        self.assertFalse(self.commands)

    async def test_submit_once_and_recall_history(self):
        self.pipe.send_text('status\r'); await self.settle()
        self.assertEqual(self.commands,['status'])
        self.assertEqual(self.ui.command.text,'')
        self.pipe.send_text('\x1b[A'); await self.settle()
        self.assertEqual(self.ui.command.text,'status')
        self.assertEqual(self.commands,['status'])

    async def test_tab_completion_and_terminal_resize(self):
        self.pipe.send_text('simulate_cap\t'); await self.settle()
        self.assertEqual(self.ui.command.text,'simulate_capture')
        self.output.rows=15
        self.ui.app._on_resize()
        await self.settle()
        self.assertEqual(self.ui.command.window.render_info.window_height,1)
        self.assertEqual(self.ui.command.text,'simulate_capture')
        self.assertFalse(self.commands)

    async def test_ctrl_c_requests_edge_shutdown(self):
        self.pipe.send_text('\x03'); await self.settle()
        self.assertEqual(self.commands,['__shutdown'])
        self.assertTrue(self.task.done())

    async def test_ctrl_d_closes_console_only(self):
        self.pipe.send_text('\x04'); await self.settle()
        self.assertEqual(self.commands,['__eof'])
        self.assertTrue(self.task.done())


if __name__=='__main__': main()
