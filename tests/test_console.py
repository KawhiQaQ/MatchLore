import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from highlights.__main__ import main
from highlights.console import Console


class ConsoleTest(unittest.TestCase):
    def test_no_command_in_pipe_shows_help_without_prompt(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out),patch('builtins.input',side_effect=AssertionError('must not prompt')):
            main([])
        self.assertIn('console',out.getvalue())

    def test_explicit_console_rejects_nonterminal(self):
        out=io.StringIO()
        with contextlib.redirect_stderr(out),patch('sys.stdin.isatty',return_value=False),self.assertRaises(SystemExit) as e:
            main(['console'])
        self.assertEqual(e.exception.code,2)
        self.assertEqual(json.loads(out.getvalue())['error']['code'],'interactive_terminal_required')

    def test_no_command_terminal_enters_console(self):
        with patch('sys.stdin.isatty',return_value=True),patch('sys.stdout.isatty',return_value=True),patch.object(Console,'run') as run:
            main([])
        run.assert_called_once()

    def test_cancel_returns_to_prompt_and_eof_exits(self):
        with tempfile.TemporaryDirectory() as root:
            console=Console(root);out=io.StringIO()
            with contextlib.redirect_stdout(out),patch('builtins.input',side_effect=['/analyze','/back','/mode dota2',KeyboardInterrupt(),EOFError()]):
                console.run()
            self.assertEqual(console.mode,'dota2')
            self.assertIn('已退出',out.getvalue())
            self.assertFalse((Path(root)/'rolling.sqlite3').exists())

    def test_cancel_ingest_never_writes(self):
        console=Console('/unused')
        with patch.object(console,'source',return_value=['--match-id','123']),patch('builtins.input',return_value='n'),patch.object(console,'call') as call,contextlib.redirect_stdout(io.StringIO()):
            console.handle('/ingest')
        call.assert_not_called()

    def test_save_preserves_full_result_and_declining_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            console=Console(root);console.last_result={'cards':[],'evidence':{'a':1}}
            target=Path(root)/'result.json'
            with patch('builtins.input',return_value=str(target)),contextlib.redirect_stdout(io.StringIO()):console.handle('/save')
            self.assertEqual(json.loads(target.read_text()),console.last_result)
            console.last_result={'other':2}
            with patch('builtins.input',side_effect=[str(target),'n']),contextlib.redirect_stdout(io.StringIO()):console.handle('/save')
            self.assertIn('evidence',json.loads(target.read_text()))

    def test_console_uses_existing_analysis_contract(self):
        console=Console('/unused');console.mode='dota2'
        with patch.object(console,'source',return_value=['--raw','/tmp/input.json']),patch('builtins.input',return_value='25'),patch.object(console,'call',return_value={'cards':[]}) as call,contextlib.redirect_stdout(io.StringIO()):
            console.handle('/analyze')
        self.assertEqual(call.call_args.args,('analyze','--mode','dota2','--raw','/tmp/input.json','--phase',1,'--minute',25,'--llm','off'))
        self.assertEqual(console.last_result,{'cards':[]})

    def test_replay_does_not_fan_out_paid_calls(self):
        console=Console('/unused');console.provider='deepseek'
        with patch('builtins.input',return_value='20'),patch.object(console,'call',return_value={'snapshots':[]}) as call,contextlib.redirect_stdout(io.StringIO()):
            console.handle('/replay 123')
        self.assertEqual(call.call_args.args[-2:],('--llm','off'))


if __name__=='__main__':unittest.main()
