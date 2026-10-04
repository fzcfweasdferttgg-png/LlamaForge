import conftest_paths  # noqa: F401
import unittest

from embers import verify

RAWS = {"a1b2c3d4e5f6": "Hi team \u2014 Sam here. I\u2019ll send the signed SOW by Friday.\nThanks!",
        "0123456789ab": "Meeting notes: budget approved by Priya on Monday."}
KINDS = ["projects", "people"]
ITEM = "it-8f2c1a2b"


def op(**over):
    o = {"op": "add", "page": "projects/acme", "kind": "loop",
         "text": "Waiting on Sam for the signed SOW", "owner": "Sam", "due": "Friday",
         "evidence": [{"raw": "a1b2c3d4e5f6", "quote": "I'll send the signed SOW by Friday"}]}
    o.update(over)
    return o


class NormaliseTest(unittest.TestCase):
    def test_typography_and_whitespace(self):
        self.assertEqual(verify.normalise("I\u2019ll  send\u2014the \u201cSOW\u201d\n"), "ill send the sow")

    def test_nfkc_folds_fullwidth(self):
        self.assertEqual(verify.normalise("\uff33\uff2f\uff37"), "sow")

    def test_none_is_empty(self):
        self.assertEqual(verify.normalise(None), "")


class CheckQuoteTest(unittest.TestCase):
    def test_verbatim_passes_despite_typography(self):
        self.assertTrue(verify.check_quote("I'll send the signed SOW", RAWS["a1b2c3d4e5f6"]))

    def test_paraphrase_fails(self):
        self.assertFalse(verify.check_quote("Sam will send the SOW", RAWS["a1b2c3d4e5f6"]))

    def test_short_quote_fails_even_if_present(self):
        self.assertFalse(verify.check_quote("Sam here", RAWS["a1b2c3d4e5f6"]))


class VerifyOpTest(unittest.TestCase):
    def test_verified_add_drops_owner_not_in_quote(self):
        clean, why = verify.verify_op(op(), RAWS, KINDS)
        self.assertIsNone(why)
        self.assertTrue(clean["verified"])
        self.assertEqual(clean["owner"], "")         # "Sam" is not inside the quote
        self.assertEqual(clean["due"], "Friday")     # "Friday" is

    def test_owner_kept_when_quoted(self):
        clean, _ = verify.verify_op(op(evidence=[{"raw": "a1b2c3d4e5f6",
                                                  "quote": "Sam here. I'll send the signed SOW by Friday"}]),
                                    RAWS, KINDS)
        self.assertEqual(clean["owner"], "Sam")

    def test_invented_due_dropped(self):
        clean, _ = verify.verify_op(op(due="2026-10-09"), RAWS, KINDS)
        self.assertEqual(clean["due"], "")

    def test_no_evidence_is_unverified_not_rejected(self):
        clean, why = verify.verify_op(op(evidence=[]), RAWS, KINDS)
        self.assertIsNone(why)
        self.assertFalse(clean["verified"])
        self.assertEqual((clean["owner"], clean["due"]), ("", ""))

    def test_unknown_raw_id_does_not_verify(self):
        clean, _ = verify.verify_op(op(evidence=[{"raw": "ffffffffffff",
                                                  "quote": "I'll send the signed SOW by Friday"}]), RAWS, KINDS)
        self.assertFalse(clean["verified"])

    def test_evidence_must_be_a_list(self):
        clean, _ = verify.verify_op(op(evidence={"raw": "a1b2c3d4e5f6"}), RAWS, KINDS)
        self.assertFalse(clean["verified"])

    def test_bad_pages_rejected(self):
        for page in ("Projects/Acme", "../x", "projects", "secrets/x", "projects/a/b", None):
            with self.subTest(page=page):
                clean, why = verify.verify_op(op(page=page), RAWS, KINDS)
                self.assertIsNone(clean)
                self.assertIn("page", why)

    def test_update_needs_known_item(self):
        self.assertIsNone(verify.verify_op(op(op="update", item=ITEM), RAWS, KINDS)[0])
        clean, _ = verify.verify_op(op(op="update", item=ITEM), RAWS, KINDS, known_items={ITEM})
        self.assertEqual(clean["item"], ITEM)

    def test_add_ignores_model_supplied_item_id(self):
        clean, _ = verify.verify_op(op(item=ITEM), RAWS, KINDS)
        self.assertIsNone(clean["item"])

    def test_close_needs_passing_evidence(self):
        clean, why = verify.verify_op(op(op="close", item=ITEM, evidence=[]), RAWS, KINDS, {ITEM})
        self.assertIsNone(clean)
        self.assertIn("evidence", why)

    def test_bad_op_and_kind(self):
        self.assertIsNone(verify.verify_op(op(op="delete"), RAWS, KINDS)[0])
        self.assertIsNone(verify.verify_op(op(kind="shell"), RAWS, KINDS)[0])
        self.assertIsNone(verify.verify_op(op(text=""), RAWS, KINDS)[0])
        self.assertIsNone(verify.verify_op("nope", RAWS, KINDS)[0])

    def test_text_collapsed_and_capped(self):
        clean, _ = verify.verify_op(op(text="a  b\n" * 400), RAWS, KINDS)
        self.assertEqual(len(clean["text"]), verify.MAX_TEXT)
        self.assertNotIn("  ", clean["text"])


class HardeningTest(unittest.TestCase):
    def test_trailing_newline_rejected_everywhere(self):
        nl = chr(10)
        for page in ("projects/acme" + nl, "projects" + nl + "/acme"):
            with self.subTest(page=page):
                self.assertIsNone(verify.verify_op(op(page=page), RAWS, KINDS)[0])
        self.assertIsNone(verify.verify_op(op(op="update", item=ITEM + nl), RAWS, KINDS,
                                           known_items={ITEM + nl})[0])
        _, pages, _ = verify.verify_batch({"new_pages": [{"page": "people/sam" + nl, "title": "x"}]},
                                          RAWS, KINDS)
        self.assertEqual(pages, [])

    def test_reserved_device_names_rejected(self):
        for page in ("projects/nul", "projects/COM1", "projects/lpt9"):
            with self.subTest(page=page):
                clean, why = verify.verify_op(op(page=page), RAWS, KINDS)
                self.assertIsNone(clean)
                self.assertIn("page", why)
        self.assertIsNone(verify.verify_op(op(page="con/acme"), RAWS, ["con"])[0])
        self.assertTrue(verify.page_ok("projects/nullable", KINDS))

    def test_unhashable_fields_do_not_crash(self):
        self.assertIsNone(verify.verify_op(op(kind=["loop"]), RAWS, KINDS)[0])
        self.assertIsNone(verify.verify_op(op(op=["add"]), RAWS, KINDS)[0])
        self.assertIsNone(verify.verify_op(op(page=["projects/acme"]), RAWS, KINDS)[0])


class BypassTest(unittest.TestCase):
    UNPAID = {"c0ffee000001": "The invoice was unpaid on Monday."}
    ITEM_ID = "it-0000abcd"

    def test_quote_cannot_start_mid_word(self):
        self.assertFalse(verify.check_quote("paid on Monday", self.UNPAID["c0ffee000001"]))
        self.assertTrue(verify.check_quote("unpaid on Monday", self.UNPAID["c0ffee000001"]))

    def test_quote_cannot_end_mid_word(self):
        self.assertFalse(verify.check_quote("The invoice was unpa", self.UNPAID["c0ffee000001"]))

    def test_mid_word_quote_does_not_verify_add(self):
        clean, _ = verify.verify_op(op(evidence=[{"raw": "c0ffee000001", "quote": "paid on Monday"}]),
                                    self.UNPAID, KINDS)
        self.assertFalse(clean["verified"])

    def test_mid_word_quote_cannot_close(self):
        clean, why = verify.verify_op(op(op="close", item=self.ITEM_ID,
                                         evidence=[{"raw": "c0ffee000001", "quote": "paid on Monday"}]),
                                      self.UNPAID, KINDS, {self.ITEM_ID})
        self.assertIsNone(clean)
        self.assertIn("evidence", why)

    def test_owner_not_grounded_by_substring(self):
        raws = {"c0ffee000002": "Signed off by the team on Monday, thanks all"}
        ev = [{"raw": "c0ffee000002", "quote": "Signed off by the team on Monday"}]
        for owner in ("Ned", "e", "team on mon"):
            with self.subTest(owner=owner):
                clean, _ = verify.verify_op(op(owner=owner, due="", evidence=ev), raws, KINDS)
                self.assertEqual(clean["owner"], "")
        clean, _ = verify.verify_op(op(owner="team", due="", evidence=ev), raws, KINDS)
        self.assertEqual(clean["owner"], "team")

    def test_due_not_grounded_by_prefix(self):
        clean, _ = verify.verify_op(op(due="Mon", owner="",
                                       evidence=[{"raw": "c0ffee000001",
                                                  "quote": "unpaid on Monday"}]), self.UNPAID, KINDS)
        self.assertEqual(clean["due"], "")

    def test_one_char_value_never_grounded(self):
        raws = {"c0ffee000003": "Plan b is to ship on Friday afternoon"}
        clean, _ = verify.verify_op(op(owner="b", due="", evidence=[{"raw": "c0ffee000003",
                                    "quote": "Plan b is to ship on Friday"}]), raws, KINDS)
        self.assertEqual(clean["owner"], "")

    def test_minus_sign_is_significant(self):
        minus = chr(0x2212)
        for sign in ("-", minus):
            with self.subTest(sign=sign):
                raw = "Balance: " + sign + "500 dollars owed by Acme"
                self.assertFalse(verify.check_quote("Balance: 500 dollars owed", raw))
                self.assertTrue(verify.check_quote("Balance: " + sign + "500 dollars owed", raw))
        self.assertTrue(verify.check_quote("Balance: -500 dollars owed",
                                           "Balance: " + minus + "500 dollars owed by Acme"))

    def test_dash_between_words_still_normalised(self):
        em = chr(0x2014)
        self.assertTrue(verify.check_quote("ship the thing - today ok", "ship the thing" + em + "today ok"))
        self.assertTrue(verify.check_quote("ship the thing" + em + "today ok", "ship the thing - today ok"))

    def test_hyphenated_word_is_one_word(self):
        raw = "non-refundable after Friday"
        self.assertFalse(verify.check_quote("refundable after Friday", raw))
        self.assertTrue(verify.check_quote("non-refundable after Friday", raw))
        self.assertTrue(verify.check_quote("Please book a follow-up call", "Please book a follow-up call soon"))
        clean, _ = verify.verify_op(op(evidence=[{"raw": "c0ffee000004", "quote": "refundable after Friday"}]),
                                    {"c0ffee000004": raw}, KINDS)
        self.assertFalse(clean["verified"])

    def test_minus_still_works_at_start_and_after_space(self):
        self.assertFalse(verify.check_quote("500 dollars owed by Acme", "-500 dollars owed by Acme"))
        self.assertTrue(verify.check_quote("-500 dollars owed by Acme", "-500 dollars owed by Acme"))
        self.assertFalse(verify.check_quote("owes 500 dollars today", "owes -500 dollars today"))
        self.assertTrue(verify.check_quote("owes -500 dollars today", "he owes -500 dollars today"))
        self.assertTrue(verify.check_quote("due 2026-10-09 sharp", "Payment due 2026-10-09 sharp"))


class VerifyBatchTest(unittest.TestCase):
    def test_op_cap(self):
        ops, _, rejected = verify.verify_batch({"ops": [op()] * 45}, RAWS, KINDS)
        self.assertEqual(len(ops), verify.MAX_OPS)
        self.assertEqual(len(rejected), 5)
        self.assertEqual(rejected[0], (40, "over the op cap"))

    def test_new_pages_validated(self):
        _, pages, _ = verify.verify_batch({"ops": [], "new_pages": [
            {"page": "people/sam", "title": "Sam (Acme)", "summary": "Client contact"},
            {"page": "secrets/x", "title": "nope", "summary": ""},
            "junk"]}, RAWS, KINDS)
        self.assertEqual(pages, [{"page": "people/sam", "title": "Sam (Acme)", "summary": "Client contact"}])

    def test_rejections_carry_reasons(self):
        _, _, rejected = verify.verify_batch({"ops": [op(page="secrets/x")]}, RAWS, KINDS)
        self.assertEqual(rejected[0][0], 0)

    def test_non_object_update(self):
        self.assertEqual(verify.verify_batch([], RAWS, KINDS), ([], [], [(-1, "update is not an object")]))


if __name__ == "__main__":
    unittest.main()
