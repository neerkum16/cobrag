import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cobrag as cg  # noqa: E402

SRC = ROOT / 'samples' / 'src'
CPY = ROOT / 'samples' / 'copy'


def fixed(*code_lines):
    """Build fixed-format source: each entry is (indicator, text) or text for Area A/B code."""
    out = []
    for ln in code_lines:
        ind, text = ln if isinstance(ln, tuple) else (' ', ln)
        out.append('      ' + ind + text)
    return '\n'.join(out) + '\n'


class PicSizing(unittest.TestCase):
    def size(self, pic, usage='DISPLAY', sep=False):
        return cg.pic_attrs(pic, usage, sep)[1]

    def test_packed(self):
        self.assertEqual(self.size('S9(11)V99', 'COMP-3'), 7)
        self.assertEqual(self.size('S9(13)V99', 'COMP-3'), 8)
        self.assertEqual(self.size('9(4)', 'COMP-3'), 3)

    def test_binary_halfword_fullword_doubleword(self):
        self.assertEqual(self.size('S9(4)', 'COMP'), 2)
        self.assertEqual(self.size('S9(5)', 'COMP'), 4)
        self.assertEqual(self.size('S9(9)', 'COMP-5'), 4)
        self.assertEqual(self.size('S9(10)', 'COMP'), 8)

    def test_zoned_and_sign_separate(self):
        self.assertEqual(self.size('9(5)V99'), 7)
        self.assertEqual(self.size('S9(7)'), 7)
        self.assertEqual(self.size('S9(7)', sep=True), 8)

    def test_edited_national(self):
        self.assertEqual(self.size('-Z(12)9.99'), 17)
        self.assertEqual(self.size('ZZ,ZZ9.99CR'), 11)
        self.assertEqual(self.size('N(10)'), 20)
        self.assertEqual(cg.pic_attrs('ZZ9', 'DISPLAY', False)[0], 'numeric-edited')


class Samples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = cg.CopyResolver([CPY])
        cls.pay = cg.parse_program(SRC / 'PAYSETL1.cbl', r)
        cls.cics = cg.parse_program(SRC / 'ACCTINQ.cbl', r)
        cls.ims = cg.parse_program(SRC / 'IMSRPT1.cbl', r)

    def para(self, prog, name):
        return prog.paras[prog.para_idx[name]]

    def test_continued_literal(self):
        self.assertEqual(self.pay.value_of('WS-BANNER'), 'PAYSETL1 NIGHTLY SETTLEMENT RUN')

    def test_layout_offsets(self):
        it = self.pay.dd['TXN-AMOUNT'][0]
        self.assertEqual((it.offset, it.length), (29, 7))
        self.assertEqual(self.pay.dd['ACCT-RECORD'][0].length, 87)
        self.assertEqual(self.pay.dd['TXN-EFF-DATE-N'][0].offset, 36)       # REDEFINES shares offset
        self.assertEqual(self.pay.dd['TXN-RECORD'][0].length, 67)          # REDEFINES adds no length
        filler = self.pay.dd['ACCT-RECORD'][0].children[-1]
        self.assertEqual(filler.offset, 57)                                # after OCCURS 3 x 8
        self.assertEqual(self.pay.dd['WS-REASON-TABLE'][0].length, 136)

    def test_reachability(self):
        self.assertFalse(self.para(self.pay, '3000-LEGACY-RECON').reachable)
        for n in ('2000-READ-NEXT', '1000-EXIT', '2000-EXIT', '9999-ABEND'):
            self.assertTrue(self.para(self.pay, n).reachable, n)
        self.assertEqual(self.para(self.pay, '0000-MAINLINE').terminal, 'GOBACK')
        self.assertIsNone(self.para(self.pay, '2000-PROCESS-TXN').terminal)   # GO TO is inside IF

    def test_crud(self):
        p = self.para(self.pay, '2300-POST-PAYMENT')
        self.assertEqual(p.crud['DB2:PAYMENT_HIST'], {'C'})
        self.assertEqual(p.crud['DB2:ACCOUNT_BAL'], {'U'})
        self.assertEqual(p.crud['FILE:ACCT-MASTER'], {'U'})
        self.assertEqual(len(p.sql), 2)            # literal 'INSERT PAYMENT_HIST FAILED' ignored
        self.assertEqual(self.para(self.pay, '2200-APPLY-HOLDS').crud['DB2:PAYMENT_HOLD'], {'R'})
        self.assertEqual(self.para(self.cics, '1000-READ-ACCOUNT').crud['CICS-FILE:ACCTMST'], {'R'})

    def test_dynamic_call_resolution(self):
        c = self.para(self.pay, '1000-INITIALIZE').calls[0]
        self.assertEqual((c['name'], c['var'], c['stable']), ('DATEUTIL', 'WS-DATE-PGM', True))

    def test_ims(self):
        d = self.para(self.ims, '0000-MAIN').dli
        self.assertEqual(d[0]['function'], 'GU')
        self.assertEqual(d[0]['segments'], ['CUSTSEG'])
        self.assertEqual(self.para(self.ims, '1000-RESCORE').crud['DLI:PCB-MASK'], {'U'})

    def test_cics_terminal_and_link(self):
        self.assertEqual(self.para(self.cics, '8000-SEND-ERROR').terminal, 'EXEC CICS RETURN/XCTL')
        self.assertEqual(self.para(self.cics, '2000-SEND-RESULT').calls[0]['name'], 'AUDITLOG')

    def test_writes(self):
        w = self.para(self.pay, '2100-VALIDATE-TXN').writes
        self.assertIn('WS-VALID-SW', w)            # SET 88 TO TRUE -> parent modified
        self.assertIn('ACCT-KEY', w)

    def test_comments_kept_in_chunks(self):
        chunks = cg.build_chunks([self.pay], 900, 3)
        c = next(x for x in chunks if x['id'] == 'PAYSETL1::PARA::3000-LEGACY-RECON::1')
        self.assertIn('RETIRED 2019', c['code'])
        self.assertIn('UNREACHABLE', c['embed_text'])

    def test_split_large_paragraph(self):
        chunks = cg.build_chunks([self.pay], 120, 2)
        parts = [x for x in chunks if x['name'] == '2300-POST-PAYMENT']
        self.assertGreater(len(parts), 1)
        for x in parts:
            self.assertIn('PARAGRAPH 2300-POST-PAYMENT | PART', x['embed_text'])


class Synthetic(unittest.TestCase):
    def build(self, src, copybooks=None):
        d = Path(tempfile.mkdtemp())
        (d / 'T.cbl').write_text(src)
        for name, text in (copybooks or {}).items():
            (d / f'{name}.cpy').write_text(text)
        return cg.parse_program(d / 'T.cbl', cg.CopyResolver([d]))

    def test_replacing_and_goto_terminal(self):
        cpy = fixed('       01  :P:-REC.', '           05  :P:-ID   PIC X(8).',
                    '           05  :P:-AMT  PIC S9(7)V99 COMP-3.')
        src = fixed('IDENTIFICATION DIVISION.', 'PROGRAM-ID. T.', 'DATA DIVISION.',
                    'WORKING-STORAGE SECTION.', '    COPY REC REPLACING ==:P:== BY ==IN==.',
                    'PROCEDURE DIVISION.', 'A-MAIN.', '    MOVE 1 TO IN-AMT',
                    '    GO TO C-END.', 'B-DEAD.', '    DISPLAY IN-ID.', 'C-END.', '    GOBACK.')
        prog = self.build(src, {'REC': cpy})
        self.assertIn('IN-AMT', prog.dd)
        self.assertEqual(prog.dd['IN-AMT'][0].length, 5)
        self.assertEqual(prog.paras[0].terminal, 'GO TO')
        self.assertFalse(prog.paras[prog.para_idx['B-DEAD']].reachable)
        self.assertTrue(prog.paras[prog.para_idx['C-END']].reachable)

    def test_section_perform_and_unresolved(self):
        src = fixed('IDENTIFICATION DIVISION.', 'PROGRAM-ID. T.', 'PROCEDURE DIVISION.',
                    'MAIN-SEC SECTION.', 'M1.', '    PERFORM WORK-SEC', '    PERFORM NO-SUCH-PARA',
                    '    STOP RUN.', 'WORK-SEC SECTION.', 'W1.', '    DISPLAY 1.', 'W2.', '    DISPLAY 2.')
        prog = self.build(src)
        self.assertTrue(all(p.reachable for p in prog.paras))
        self.assertIn('PERFORM NO-SUCH-PARA', prog.paras[prog.para_idx['M1']].unresolved)


class SqlParsing(unittest.TestCase):
    def test_join_and_subquery(self):
        m, l = cg.mask_literals("SELECT A.X INTO :H-X FROM T1 A JOIN T2 B ON A.K = B.K, T3 C "
                                "WHERE C.S = 'Y' AND A.K IN (SELECT K FROM T4)")
        info = cg.analyze_sql(m, l)
        self.assertEqual(set(info['tables']), {'T1', 'T2', 'T3', 'T4'})
        self.assertEqual(info['into_vars'], ['H-X'])

    def test_delete_and_update_subselect(self):
        m, l = cg.mask_literals('UPDATE ACCT SET X = 1 WHERE K IN (SELECT K FROM HOLDS)')
        self.assertEqual(cg.analyze_sql(m, l)['tables'], {'ACCT': {'U'}, 'HOLDS': {'R'}})
        m, l = cg.mask_literals('DELETE FROM HIST WHERE D < CURRENT DATE')
        self.assertEqual(cg.analyze_sql(m, l)['tables'], {'HIST': {'D'}})


class Retrieval(unittest.TestCase):
    def test_cli_analyze_smoke(self):
        cg.main(['analyze', str(SRC), '-I', str(CPY), '-o', str(Path(tempfile.mkdtemp()) / 'o')])

    def test_bm25_ranking(self):
        r = cg.CopyResolver([CPY])
        chunks = cg.build_chunks([cg.parse_program(SRC / 'PAYSETL1.cbl', r)], 900, 3)
        top = cg.bm25_search(chunks, 'which paragraph writes the reject file', k=1, kinds={'paragraph'})
        self.assertEqual(top[0][1]['name'], '2800-WRITE-REJECT')


if __name__ == '__main__':
    unittest.main(verbosity=2)
