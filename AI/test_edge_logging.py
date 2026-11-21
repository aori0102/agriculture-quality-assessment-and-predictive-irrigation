import io
import logging
import unittest
from contextlib import redirect_stderr

from edge_logging import EdgeFormatter, get_logger


class LoggingTests(unittest.TestCase):
    def test_repeated_setup_does_not_duplicate_lines_or_change_root(self):
        handlers = list(logging.getLogger().handlers)
        out = io.StringIO()
        with redirect_stderr(out):
            get_logger('MQTT')
            get_logger('MQTT').info('connected')
        self.assertEqual(out.getvalue().count('connected'), 1)
        self.assertIn('[Mqtt] connected', out.getvalue())
        self.assertEqual(logging.getLogger().handlers, handlers)

    def test_untrusted_multiline_input_cannot_forge_log_lines(self):
        record = logging.LogRecord('edge.telemetry', logging.WARNING, '', 0,
                                   'bad node=%s', ('one\nFAKE\x1b[31m',), None)
        with redirect_stderr(io.StringIO()):
            text = EdgeFormatter().format(record)
        self.assertNotIn('\n', text)
        self.assertNotIn('\x1b', text)
        self.assertIn('[Telemetry]', text)
        self.assertNotIn('WARNING', text)

    def test_debug_hidden_at_default_and_cloud_uses_same_format(self):
        out = io.StringIO()
        with redirect_stderr(out):
            get_logger('MODEL').debug('hidden detail')
            logging.getLogger('cloud_edge.edge_pump').warning('node=one rejected')
        self.assertNotIn('hidden detail', out.getvalue())
        self.assertIn('[Cloud]', out.getvalue())
        self.assertNotIn('WARNING', out.getvalue())
