import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum import local_cell_transfer as transfer


class TransferInputTests(unittest.TestCase):
    def test_cli_reads_valid_inputs_and_rejects_large_files_before_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            genome, graph = root / 'genome.json', root / 'graph.json'
            genome.write_text('{"seed": 1}', encoding='utf-8')
            graph.write_text('{"graph": 2}', encoding='utf-8')
            args = ['--source', str(root / 'source'), '--destination', str(root / 'dest'),
                    '--genome', str(genome), '--graph', str(graph),
                    '--expected-source-head', 'x']
            with patch.object(transfer, 'transfer_journal', return_value={'ok': True}) as run:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(transfer.transfer_cli(transfer.V2A_TO_V2B, args), 0)
                self.assertEqual(run.call_args.kwargs['seed'], {'seed': 1})
                self.assertEqual(run.call_args.kwargs['graph'], {'graph': 2})
            for target in (genome, graph):
                original = target.read_bytes()
                target.write_bytes(b' ' * 65)
                with self.subTest(target=target.name), patch.object(transfer, 'MAX_TRANSFER_INPUT_BYTES', 64), \
                     patch.object(transfer, 'transfer_journal') as run, contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(transfer.transfer_cli(transfer.V2A_TO_V2B, args), 1)
                    self.assertIn('exceeds byte limit', err.getvalue())
                    run.assert_not_called()
                    self.assertFalse((root / 'dest').exists())
                target.write_bytes(original)

    @unittest.skipUnless(hasattr(os, 'mkfifo') and getattr(os, 'O_NONBLOCK', 0), 'requires POSIX FIFO')
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / 'fifo'
            os.mkfifo(fifo)
            script = ('from mojum.local_cell_transfer import _read_transfer_json; '
                      'import sys; _read_transfer_json(sys.argv[1])')
            result = subprocess.run([sys.executable, '-c', script, str(fifo)],
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('regular file', result.stderr)

    def test_regular_json_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.json'
            path.write_bytes(json.dumps({'name': 'value'}).encode('utf-8'))
            self.assertEqual(transfer._read_transfer_json(path), {'name': 'value'})
            path.write_bytes(b'\xff')
            with self.assertRaises(UnicodeDecodeError):
                transfer._read_transfer_json(path)


if __name__ == '__main__':
    unittest.main()
