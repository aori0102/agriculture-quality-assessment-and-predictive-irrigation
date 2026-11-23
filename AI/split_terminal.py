"""Fixed command input below a bounded, independently scrolling log pane."""
from collections import deque
from threading import Event, RLock
from contextlib import contextmanager
import re
import sys
import logging

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


class LogStream:
    """Thread-safe text sink, bounded even when a producer never writes newline."""
    encoding = 'utf-8'
    errors = 'replace'
    _ansi = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')

    def __init__(self, max_lines=3000, max_width=8000):
        self.lines = deque(maxlen=max_lines)
        self.partial = ''
        self.max_width = max_width
        self.revision = 0
        self.lock = RLock()

    def write(self, value):
        raw = str(value)
        clean = self._ansi.sub('', raw).replace('\r', '\n')
        clean = ''.join(c for c in clean if c in '\n\t' or ord(c) >= 32 and ord(c) != 127)
        with self.lock:
            chunks = (self.partial + clean).split('\n')
            for line in chunks[:-1]:
                self.lines.append(line[:self.max_width])
            self.partial = chunks[-1][-self.max_width:]
            self.revision += 1
        return len(raw)

    def flush(self):
        pass

    def isatty(self):
        return False

    def snapshot(self):
        with self.lock:
            return self.revision, '\n'.join([*self.lines, self.partial])


class SplitTerminal:
    def __init__(self, enqueue, *, input=None, output=None):
        self.enqueue = enqueue
        self.stream = LogStream()
        self.follow = True
        self.stopping = Event()
        self.last_revision = -1
        self.original_stdout, self.original_stderr = sys.stdout, sys.stderr
        self.log = TextArea(read_only=True, scrollbar=True, wrap_lines=False,
                            focusable=False, style='class:log')
        self.command = TextArea(height=1, prompt='debug> ', multiline=False,
            history=InMemoryHistory(), auto_suggest=AutoSuggestFromHistory(),
            completer=WordCompleter(['simulate_pump', 'simulate_capture',
                'simulate_node_connection_lost', 'help', 'status', 'cancel'], ignore_case=True),
            complete_while_typing=False, accept_handler=self.submit, style='class:command')
        bindings = KeyBindings()

        @bindings.add('tab')
        def complete_command(event):
            buffer = self.command.buffer
            if buffer.complete_state:
                buffer.complete_next()
            else:
                buffer.start_completion(select_first=True)

        @bindings.add('up')
        def previous_command(event):
            self.command.buffer.cancel_completion()
            self.command.buffer.history_backward()

        @bindings.add('down')
        def next_command(event):
            self.command.buffer.cancel_completion()
            self.command.buffer.history_forward()

        @bindings.add('c-c')
        def shutdown(event):
            self.enqueue('__shutdown')
            event.app.exit()

        @bindings.add('c-d')
        def close_console(event):
            self.enqueue('__eof')
            event.app.exit()

        @bindings.add('pageup')
        def page_up(event):
            self.scroll(-10)

        @bindings.add('pagedown')
        def page_down(event):
            self.scroll(10)

        @bindings.add('c-end')
        def follow_log(event):
            self.follow = True
            self.last_revision = -1

        header = Window(FormattedTextControl(' EDGE AI  |  DEBUG CONSOLE  |  Hardware connected'),
                        height=1, style='class:header')
        separator = Window(FormattedTextControl(lambda: (
            ' LOG  [following]' if self.follow else ' LOG  [paused: Ctrl+End to follow]')),
            height=1, style='class:separator')
        footer = Window(FormattedTextControl(
            ' Enter: send | Tab: complete | Up/Down: history | PgUp/PgDn: logs | Ctrl+C: stop Edge'),
            height=1, style='class:footer')
        self.app = Application(layout=Layout(HSplit([header, self.log, separator, self.command, footer]),
            focused_element=self.command), key_bindings=bindings, full_screen=True,
            input=input, output=output, refresh_interval=.1, min_redraw_interval=.05,
            before_render=self.refresh, style=Style.from_dict({
                'header': 'bg:#163d47 #c8f5e1 bold',
                'log': '#d7e1e5 bg:#101820',
                'separator': 'bg:#20313b #8eb9ba',
                'command': 'bg:#15262b #effff5',
                'footer': 'bg:#163d47 #b1d3ce',
                'completion-menu.completion': 'bg:#20313b #d7e1e5',
                'completion-menu.completion.current': 'bg:#3b6b60 #ffffff',
            }))

    def submit(self, buffer):
        line = buffer.text.strip()
        if line:
            self.stream.write(f'debug> {line}\n')
            self.enqueue(line)
        return False  # Prompt toolkit clears only the command buffer.

    def refresh(self, app=None):
        revision, text = self.stream.snapshot()
        if revision == self.last_revision:
            return
        self.last_revision = revision
        cursor = len(text) if self.follow else min(self.log.buffer.cursor_position, len(text))
        self.log.buffer.set_document(Document(text, cursor_position=cursor), bypass_readonly=True)

    def scroll(self, lines):
        self.follow = False
        doc = self.log.buffer.document
        target = max(0, min(doc.line_count - 1, doc.cursor_position_row + lines))
        self.log.buffer.cursor_position = doc.translate_row_col_to_index(target, 0)

    @contextmanager
    def captured_streams(self):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        redirected = []
        sys.stdout = sys.stderr = self.stream
        try:
            # Libraries can bind their StreamHandler before debug starts.
            # Route those too, while leaving log files untouched.
            loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
            seen = set()
            for logger in loggers:
                if not isinstance(logger, logging.Logger):
                    continue
                for handler in logger.handlers:
                    if (id(handler) not in seen and isinstance(handler, logging.StreamHandler)
                            and not isinstance(handler, logging.FileHandler)
                            and getattr(handler, 'stream', None) in (old_stdout, old_stderr)):
                        seen.add(id(handler))
                        previous = handler.setStream(self.stream)
                        redirected.append((handler, previous))
            yield
        finally:
            for handler, previous in redirected:
                if handler.stream is self.stream:
                    handler.setStream(previous)
            if sys.stdout is self.stream:
                sys.stdout = old_stdout
            if sys.stderr is self.stream:
                sys.stderr = old_stderr

    def run(self):
        # Application output was created before capturing streams, so the
        # renderer writes directly to the terminal rather than back to itself.
        with self.captured_streams():
            def ready():
                if self.stopping.is_set():
                    self.app.exit()
            self.app.run(pre_run=ready, handle_sigint=False)

    def stop(self):
        self.stopping.set()
        loop = self.app.loop
        if loop is not None and self.app.is_running:
            def exit_if_running():
                if self.app.is_running and not self.app.is_done:
                    self.app.exit()
            try:
                loop.call_soon_threadsafe(exit_if_running)
            except RuntimeError:
                pass
