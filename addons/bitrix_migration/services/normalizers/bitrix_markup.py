"""Convert Bitrix BBCode-like markup to clean HTML for Odoo.

Supported tags:
  [B]...[/B]           → <strong>...</strong>
  [I]...[/I]           → <em>...</em>
  [U]...[/U]           → <u>...</u>
  [S]...[/S]           → <s>...</s>
  [URL=...]...[/URL]   → <a href="...">...</a>
  [URL]...[/URL]       → <a href="...">...</a>
  [IMG]...[/IMG]       → <img src="..."/>
  [USER=ID]Name[/USER] → <strong>Name</strong> (or resolved name from employee_map)
  [DISK FILE ID=...]   → <em>📎 файл (см. вложения)</em>
  [LIST] [*] [/LIST]   → <ul><li>...</li></ul>
  [QUOTE]...[/QUOTE]   → <blockquote>...</blockquote>
  [CODE]...[/CODE]     → <pre><code>...</code></pre>
  [SIZE=N]...[/SIZE]   → <span style="font-size:Npx">...</span>
  [COLOR=X]...[/COLOR] → <span style="color:X">...</span>
  [TABLE]...[/TABLE]   → <table>...</table>
  [TR]...[/TR]         → <tr>...</tr>
  [TD]...[/TD]         → <td>...</td>
  [TH]...[/TH]         → <th>...</th>
  Newlines             → <br/>
"""
import re


def normalize_bitrix_markup(text, employee_name_map=None):
    """Convert Bitrix markup to HTML.

    Args:
        text: Raw Bitrix text with BBCode-like markup.
        employee_name_map: Optional dict {str(bitrix_user_id): 'Display Name'}
            for resolving [USER=ID] tags to real names.

    Returns:
        Cleaned HTML string suitable for Odoo rich-text fields.
    """
    if not text:
        return text

    employee_name_map = employee_name_map or {}

    # [USER=ID]Name[/USER] → resolved name or original name in bold; empty → ''
    def _replace_user(match):
        user_id = match.group(1)
        inner_name = match.group(2).strip()
        resolved = employee_name_map.get(str(user_id))
        name = resolved or inner_name
        if not name:
            return ''
        return f'<strong>{_escape(name)}</strong>'

    text = re.sub(
        r'\[USER=(\d+)\](.*?)\[/USER\]',
        _replace_user,
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # [DISK FILE ID=...] — keep a visible marker while files are handled as attachments.
    text = re.sub(
        r'\[DISK\s+FILE\s+ID=(\d+)\]',
        r'<em>📎 файл (см. вложения)</em>',
        text,
        flags=re.IGNORECASE,
    )

    # Simple paired tags
    _SIMPLE_TAGS = [
        (r'\[B\](.*?)\[/B\]', r'<strong>\1</strong>'),
        (r'\[I\](.*?)\[/I\]', r'<em>\1</em>'),
        (r'\[U\](.*?)\[/U\]', r'<u>\1</u>'),
        (r'\[S\](.*?)\[/S\]', r'<s>\1</s>'),
        (r'\[QUOTE\](.*?)\[/QUOTE\]', r'<blockquote>\1</blockquote>'),
        (r'\[CODE\](.*?)\[/CODE\]', r'<pre><code>\1</code></pre>'),
        (r'\[TABLE\](.*?)\[/TABLE\]', r'<table>\1</table>'),
        (r'\[TR\](.*?)\[/TR\]', r'<tr>\1</tr>'),
        (r'\[TD\](.*?)\[/TD\]', r'<td>\1</td>'),
        (r'\[TH\](.*?)\[/TH\]', r'<th>\1</th>'),
    ]
    for pattern, replacement in _SIMPLE_TAGS:
        text = re.sub(pattern, replacement, text, flags=re.DOTALL | re.IGNORECASE)

    # [URL=href]text[/URL]
    text = re.sub(
        r'\[URL=(.*?)\](.*?)\[/URL\]',
        r'<a href="\1">\2</a>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # [URL]href[/URL]
    text = re.sub(
        r'\[URL\](.*?)\[/URL\]',
        r'<a href="\1">\1</a>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # [IMG]src[/IMG]
    text = re.sub(
        r'\[IMG\](.*?)\[/IMG\]',
        r'<img src="\1"/>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # [SIZE=N]...[/SIZE]
    text = re.sub(
        r'\[SIZE=(\d+)\](.*?)\[/SIZE\]',
        r'<span style="font-size:\1px">\2</span>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # [COLOR=X]...[/COLOR]
    text = re.sub(
        r'\[COLOR=(#?[a-zA-Z0-9]+)\](.*?)\[/COLOR\]',
        r'<span style="color:\1">\2</span>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # [LIST] with [*] items [/LIST]
    def _replace_list(match):
        inner = match.group(1)
        items = re.split(r'\[\*\]', inner)
        items = [item.strip() for item in items if item.strip()]
        if not items:
            return ''
        li_items = ''.join(f'<li>{item}</li>' for item in items)
        return f'<ul>{li_items}</ul>'

    text = re.sub(
        r'\[LIST\](.*?)\[/LIST\]',
        _replace_list,
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Remove any remaining unknown BBCode tags
    text = re.sub(r'\[/?[A-Z_]+(?:=[^\]]+)?\]', '', text, flags=re.IGNORECASE)

    # Convert newlines to <br/> (but not inside <pre>)
    if '<pre>' not in text:
        text = text.replace('\n', '<br/>')
    else:
        # Split around pre blocks and only convert newlines outside them
        parts = re.split(r'(<pre>.*?</pre>)', text, flags=re.DOTALL)
        converted = []
        for part in parts:
            if part.startswith('<pre>'):
                converted.append(part)
            else:
                converted.append(part.replace('\n', '<br/>'))
        text = ''.join(converted)

    # Clean up excessive <br/> runs
    text = re.sub(r'(<br/>\s*){3,}', '<br/><br/>', text)

    return text.strip()


def _escape(text):
    """Minimal HTML escaping for user-supplied text inserted into HTML."""
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def build_employee_name_map(env):
    """Build {str(bitrix_user_id): 'Full Name'} from hr.employee records.

    Used to resolve [USER=ID] tags in Bitrix markup.
    """
    Employee = env['hr.employee'].sudo().with_context(active_test=False)
    employees = Employee.search([('x_bitrix_id', '!=', 0)])
    return {
        str(emp.x_bitrix_id): emp.name
        for emp in employees
        if emp.x_bitrix_id and emp.name
    }
