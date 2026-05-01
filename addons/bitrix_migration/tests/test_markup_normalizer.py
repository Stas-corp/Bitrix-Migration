from odoo.tests.common import TransactionCase

from ..services.normalizers.bitrix_markup import normalize_bitrix_markup


class TestMarkupNormalizer(TransactionCase):
    """Tests for Bitrix BBCode → HTML normalizer (2.01–2.03)."""

    # ── Basic formatting tags ───────────────────────────────────────

    def test_bold(self):
        self.assertEqual(
            normalize_bitrix_markup('[B]hello[/B]'),
            '<strong>hello</strong>',
        )

    def test_italic(self):
        self.assertEqual(
            normalize_bitrix_markup('[I]hello[/I]'),
            '<em>hello</em>',
        )

    def test_underline(self):
        self.assertEqual(
            normalize_bitrix_markup('[U]hello[/U]'),
            '<u>hello</u>',
        )

    def test_strikethrough(self):
        self.assertEqual(
            normalize_bitrix_markup('[S]hello[/S]'),
            '<s>hello</s>',
        )

    # ── Links ───────────────────────────────────────────────────────

    def test_url_with_href(self):
        result = normalize_bitrix_markup('[URL=https://example.com]Click[/URL]')
        self.assertIn('href="https://example.com"', result)
        self.assertIn('>Click</a>', result)

    def test_url_bare(self):
        result = normalize_bitrix_markup('[URL]https://example.com[/URL]')
        self.assertIn('href="https://example.com"', result)

    # ── Images ──────────────────────────────────────────────────────

    def test_img(self):
        result = normalize_bitrix_markup('[IMG]https://example.com/pic.png[/IMG]')
        self.assertIn('<img src="https://example.com/pic.png"/>', result)

    # ── USER tag resolution ─────────────────────────────────────────

    def test_user_tag_with_map(self):
        """[USER=ID] resolved to real name from employee map."""
        emp_map = {'566': 'Юлія Доманська'}
        result = normalize_bitrix_markup(
            '[USER=566]Юля[/USER]', emp_map,
        )
        self.assertIn('Юлія Доманська', result)
        self.assertNotIn('[USER=', result)

    def test_user_tag_without_map(self):
        """[USER=ID] falls back to inner name if not in map."""
        result = normalize_bitrix_markup('[USER=999]Іван[/USER]')
        self.assertIn('Іван', result)
        self.assertNotIn('[USER=', result)

    def test_user_tag_empty(self):
        """Empty [USER=ID][/USER] produces empty string, not 'User #ID'."""
        result = normalize_bitrix_markup('[USER=123][/USER]')
        self.assertNotIn('[USER=', result)
        self.assertNotIn('User #', result)
        self.assertEqual(result, '')

    def test_user_tag_empty_no_map_no_inner(self):
        """Empty USER tag with no map and no inner text returns empty string."""
        result = normalize_bitrix_markup('before [USER=999][/USER] after')
        self.assertNotIn('User #', result)
        self.assertIn('before', result)
        self.assertIn('after', result)

    # ── Lists ───────────────────────────────────────────────────────

    def test_list(self):
        result = normalize_bitrix_markup('[LIST][*]one[*]two[/LIST]')
        self.assertIn('<ul>', result)
        self.assertIn('<li>one</li>', result)
        self.assertIn('<li>two</li>', result)

    # ── Quote and Code ──────────────────────────────────────────────

    def test_quote(self):
        result = normalize_bitrix_markup('[QUOTE]text[/QUOTE]')
        self.assertIn('<blockquote>text</blockquote>', result)

    def test_code(self):
        result = normalize_bitrix_markup('[CODE]print("hi")[/CODE]')
        self.assertIn('<pre><code>print("hi")</code></pre>', result)

    # ── Color and Size ──────────────────────────────────────────────

    def test_color(self):
        result = normalize_bitrix_markup('[COLOR=#ff0000]red[/COLOR]')
        self.assertIn('style="color:#ff0000"', result)

    def test_size(self):
        result = normalize_bitrix_markup('[SIZE=18]big[/SIZE]')
        self.assertIn('style="font-size:18px"', result)

    # ── Table ───────────────────────────────────────────────────────

    def test_table(self):
        result = normalize_bitrix_markup(
            '[TABLE][TR][TH]Col1[/TH][TD]Val1[/TD][/TR][/TABLE]',
        )
        self.assertIn('<table>', result)
        self.assertIn('<th>Col1</th>', result)
        self.assertIn('<td>Val1</td>', result)

    # ── DISK FILE placeholder ───────────────────────────────────────

    def test_disk_file_removed(self):
        result = normalize_bitrix_markup('See file [DISK FILE ID=42] here')
        self.assertNotIn('[DISK', result)
        self.assertNotIn('FILE ID', result)
        self.assertIn('файл', result)
        self.assertIn('See file', result)
        self.assertIn('here', result)

    def test_disk_file_only_produces_placeholder(self):
        result = normalize_bitrix_markup('[DISK FILE ID=42]')
        self.assertTrue(result)
        self.assertIn('файл', result)
        self.assertNotIn('[DISK', result)

    def test_multiple_disk_files(self):
        result = normalize_bitrix_markup('[DISK FILE ID=42][DISK FILE ID=43]')
        self.assertEqual(result.count('файл'), 2)
        self.assertNotIn('[DISK', result)

    # ── Newlines ────────────────────────────────────────────────────

    def test_newlines_to_br(self):
        result = normalize_bitrix_markup('line1\nline2')
        self.assertIn('<br/>', result)

    def test_newlines_preserved_in_code(self):
        """Newlines inside [CODE] blocks are NOT converted to <br/>."""
        result = normalize_bitrix_markup('[CODE]a\nb[/CODE]')
        self.assertIn('a\nb', result)

    # ── Edge cases ──────────────────────────────────────────────────

    def test_empty_input(self):
        self.assertEqual(normalize_bitrix_markup(''), '')
        self.assertIsNone(normalize_bitrix_markup(None))

    def test_plain_text_unchanged(self):
        self.assertEqual(normalize_bitrix_markup('plain text'), 'plain text')

    def test_case_insensitive(self):
        self.assertEqual(
            normalize_bitrix_markup('[b]hello[/b]'),
            '<strong>hello</strong>',
        )

    def test_nested_tags(self):
        result = normalize_bitrix_markup('[B][I]bold italic[/I][/B]')
        self.assertIn('<strong>', result)
        self.assertIn('<em>', result)

    def test_unknown_tags_stripped(self):
        result = normalize_bitrix_markup('[UNKNOWN]text[/UNKNOWN]')
        self.assertEqual(result, 'text')

    def test_multiple_br_collapsed(self):
        """Excessive newlines are collapsed to max 2 <br/>."""
        result = normalize_bitrix_markup('a\n\n\n\n\nb')
        self.assertNotIn('<br/><br/><br/>', result)
