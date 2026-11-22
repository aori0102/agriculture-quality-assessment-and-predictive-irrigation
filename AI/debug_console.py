"""Optional terminal commands; all actions execute on the Edge main loop."""
from dataclasses import dataclass
from queue import Queue, Empty
from threading import Thread
from time import monotonic, time
from uuid import uuid4
import json
import shlex

from action_index import ActionIndex
from edge_logging import get_logger

logger = get_logger('DEBUG')


@dataclass
class Simulation:
    id: str
    started: float
    delay_ms: int
    phase: str = 'arming'


class DebugConsole:
    TIMEOUT = 120.0
    HELP = ('simulate_pump <node_id> <duration_ms> [delay_ms=5000]\n'
            'simulate_capture <node_id>\n'
            'simulate_node_connection_lost <node_id> [after_pump_delay_ms=5000]\n'
            'help | status | cancel <node_id>')

    def __init__(self, fallback, pump, proxy, profiles, *, clock=monotonic):
        self.fallback, self.pump, self.proxy, self.profiles = fallback, pump, proxy, profiles
        self.clock = clock
        self.commands = Queue(maxsize=64)
        self.scheduled = {}
        self.simulations = {}
        self.closed = False
        self.ui = None

    def start(self):
        logger.info('Debug console enabled. Commands actuate real hardware.\n%s', self.HELP)
        Thread(target=self._read, name='debug-console', daemon=True).start()

    def _read(self):
        import sys
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                from split_terminal import SplitTerminal
                self.ui = SplitTerminal(self.enqueue)
                self.ui.stream.write('Type help for commands. No simulation runs until you enter a command.\n')
                if self.closed:
                    return
                self.ui.run()
                return
            except EOFError:
                self.enqueue('__eof')
                return
            except (ImportError, OSError, RuntimeError) as error:
                logger.warning('Split terminal unavailable (%s); using plain console', type(error).__name__)
        while not self.closed:
            try:
                line = input('debug> ')
            except (EOFError, OSError):
                self.enqueue('__eof')
                return
            self.enqueue(line)

    def enqueue(self, line):
        from queue import Full
        try:
            self.commands.put_nowait(line)
            return True
        except Full:
            logger.warning('Debug command queue full; command rejected')
            return False

    @staticmethod
    def milliseconds(value, minimum, maximum):
        try:
            result = int(value)
        except (ValueError, TypeError):
            raise ValueError('Duration/delay must be an integer in milliseconds') from None
        if not minimum <= result <= maximum:
            raise ValueError(f'Milliseconds must be between {minimum} and {maximum}')
        return result

    def execute(self, line):
        parts = shlex.split(line)
        if not parts:
            return
        command = parts[0]
        if command == '__shutdown':
            raise KeyboardInterrupt
        if command == '__eof':
            self.close()
            logger.info('Console input closed; debug simulations cancelled; Edge keeps running')
            return
        if command == 'help' and len(parts) == 1:
            logger.info('\n%s', self.HELP)
            return
        if command == 'status' and len(parts) == 1:
            logger.info('scheduled_pumps=%s simulations=%s', list(self.scheduled),
                        {n: s.phase for n, s in self.simulations.items()})
            return
        arity = {'simulate_pump': (3, 4), 'simulate_capture': (2,),
                 'simulate_node_connection_lost': (2, 3), 'cancel': (2,)}
        if command not in arity or len(parts) not in arity[command]:
            raise ValueError('Unknown command or arguments; type help')
        node = parts[1]
        if node not in self.profiles.read_all_recorded_nodes():
            raise ValueError('Unknown node_id')
        if command == 'cancel':
            self.scheduled.pop(node, None)
            self.finish(node, 'cancelled')
            return
        if node in self.simulations or node in self.scheduled:
            raise ValueError('Node already has a pending debug operation; cancel it first')
        if command == 'simulate_pump':
            duration = self.milliseconds(parts[2], 1, self.pump.max_duration_ms)
            delay = self.milliseconds(parts[3] if len(parts) == 4 else '5000', 0, 120000)
            self.scheduled[node] = (self.clock() + delay / 1000, duration)
            logger.info('PUMP SCHEDULED node=%s duration_ms=%s delay_ms=%s', node, duration, delay)
        elif command == 'simulate_capture':
            queued = self.proxy.send_command(node, ActionIndex.Capture, ttl_ms=10000)
            logger.info('CAPTURE node=%s queued=%s (await node confirmation)', node, queued)
        else:
            delay = self.milliseconds(parts[2] if len(parts) == 3 else '5000', 0, 120000)
            config = self.fallback.state['configs'].get(node, {}).get('body', {})
            if not config.get('fallback_enabled'):
                profile = self.profiles.get_node_profile(node)
                if profile is not None and profile.auto_irrigation_enabled is not True:
                    raise ValueError('Auto irrigation is OFF; enable Auto and wait for node configuration ACK')
                raise ValueError('Automatic fallback config is not active yet; wait for configuration sync and check limits')
            if not self.fallback.control_ready(node):
                raise ValueError('Node config/audit sync is incomplete or stale; wait for a fresh config_applied ACK')
            if node in self.pump.pending:
                raise ValueError('Node has a pending pump command; wait for its confirmation')
            if self.pump.confirmed.get(node) is not False:
                raise ValueError('Pump is ON or its state is unknown; need fresh confirmed OFF')
            simulation = Simulation(uuid4().hex, self.clock(), delay)
            payload = dict(edge_id=self.fallback.edge_id, node_id=node, action='arm',
                simulation_id=simulation.id, session_id=self.fallback.session,
                after_pump_delay_ms=delay, expires_at_epoch=int(time()) + 10)
            if not self.fallback.publish(node, 'debug', payload):
                raise ValueError('Could not queue simulation request')
            self.simulations[node] = simulation
            # Block commands immediately; heartbeat is paused after node arms.
            self.fallback.debug_blocked.add(node)
            logger.info('LOSS ARMING node=%s id=%s timeout=120s', node, simulation.id)

    def receive(self, message, node):
        if message.retain:
            return False
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(data, dict) or not str(data.get('event', '')).startswith('debug_simulation_'):
            return False
        simulation = self.simulations.get(node)
        if (simulation is None or data.get('node_id') != node
                or data.get('edge_id') != self.fallback.edge_id
                or data.get('session_id') != self.fallback.session
                or data.get('simulation_id') != simulation.id):
            return True  # Stale debug acknowledgements never affect normal state.
        if self.clock() - simulation.started >= self.TIMEOUT:
            self.finish(node, 'timeout')
        elif data['event'] == 'debug_simulation_rejected':
            self.finish(node, 'node rejected simulation')
        elif data['event'] == 'debug_simulation_armed' and simulation.phase == 'arming':
            simulation.phase = 'waiting_for_local_pump'
            self.fallback.debug_suspended.add(node)
            self.fallback.ready.pop(node, None)
            self.fallback.pending.pop(node, None)
            logger.info('LOSS ACTIVE node=%s | config/heartbeat suspended; normal fallback conditions apply', node)
        elif (data['event'] == 'debug_simulation_completed' and simulation.phase != 'arming'
              and data.get('pump_running') is False and data.get('source') == 'local_fallback'
              # Measured stop observation can overshoot a 60s timer by a loop tick.
              and type(data.get('actual_duration_ms')) is int and 0 < data['actual_duration_ms'] <= 120000
              and isinstance(data.get('run_id'), str) and data['run_id']):
            self.finish(node, 'node ACK after completed local pump and delay')
        return True

    def finish(self, node, reason):
        simulation = self.simulations.pop(node, None)
        self.fallback.debug_suspended.discard(node)
        self.fallback.debug_blocked.discard(node)
        if simulation is None:
            return
        self.fallback.ready.pop(node, None)
        self.fallback.pending.pop(node, None)
        try:
            self.fallback.publish(node, 'debug', dict(edge_id=self.fallback.edge_id, node_id=node,
                action='cancel', session_id=self.fallback.session, simulation_id=simulation.id))
        except Exception as error:
            logger.warning('Debug cancel could not be queued: node=%s type=%s', node, type(error).__name__)
        self.fallback.last_tick = -float('inf')
        logger.info('LOSS END node=%s reason=%s | heartbeat restored; readiness must be reacquired', node, reason)

    def tick(self):
        if self.closed:
            return
        for _ in range(32):
            try:
                line = self.commands.get_nowait()
            except Empty:
                break
            try:
                self.execute(line)
            except (ValueError, TypeError) as error:
                logger.warning('Command rejected: %s', error)
            except Exception as error:
                logger.error('Debug command failed: type=%s', type(error).__name__)
            finally:
                self.commands.task_done()
            if self.closed:
                return
        now = self.clock()
        for node, (due, duration) in tuple(self.scheduled.items()):
            if now >= due:
                self.scheduled.pop(node)
                if now - due > 2:
                    logger.warning('PUMP REJECTED node=%s | scheduled dispatch expired', node)
                    continue
                try:
                    queued = self.pump.request_debug_start(node, duration)
                except Exception as error:
                    logger.error('PUMP DISPATCH FAILED node=%s type=%s', node, type(error).__name__)
                    queued = False
                if queued:
                    logger.info('PUMP DISPATCH node=%s | command sent; waiting for node result', node)
                else:
                    logger.warning('PUMP DISPATCH node=%s | not sent; see DEBUG PUMP NOT SENT / COMMAND NOT SENT reason above', node)
        for node, simulation in tuple(self.simulations.items()):
            elapsed = now - simulation.started
            if elapsed >= self.TIMEOUT or (simulation.phase == 'arming' and elapsed >= 10):
                self.finish(node, '120s timeout' if elapsed >= self.TIMEOUT else 'node arm ACK timeout')

    def close(self):
        self.closed = True
        if self.ui is not None:
            self.ui.stop()
        self.scheduled.clear()
        for node in tuple(self.simulations):
            self.finish(node, 'debug console closed')
