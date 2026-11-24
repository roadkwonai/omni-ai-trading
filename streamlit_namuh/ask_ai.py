"""'AI에게 묻기' from any screen (docs/ai-ask-anywhere-plan.md, user 2026-11-24).

Dragged text, a term, a screen or a card fills the floating chat's input box and shows what the service already says about it.
Nothing is sent until the user presses send, so nothing is paid by accident and money amounts can be seen to be hidden.
"""
from __future__ import annotations

import re

import streamlit as st
from streamlit.errors import StreamlitAPIException

import dialogs

BOX = 'quick_liner_fill'  # text for the chat input; set into the box in the run that draws it (a rerun in between would send it)
LIMIT = 80
# amounts (a comma or a unit after them), counts, ratios, and codes or account numbers (6 digits or more, dashes too);
# '2026-11-24', 'KODEX 200 주가' and '5주년' stay
NUMBERS = re.compile(
    r'(?<![\d.,:-])(?!(?:19|20)\d\d-\d\d?-\d\d?(?!\d))(?=(?:-?\d){7})\d+(?:-\d+){1,3}'
    r'|(?<![\d.,:-])[+−-]?\d[\d,]*(?:\.\d+)?(?=\s*(?:%|[억만천]|원)|주(?!년|간|일|차|봉)|\s+주(?!년|간|일|가|식|문|도|요|주|말|차|봉|력|권))'
    r'|(?<![\d.,:-])[+−-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d-])\d{6,}(?!\d)')
WORD = re.compile(r'[가-힣A-Za-z]{2,}')
SENTENCE = re.compile(r'(?:니다|[가-힣]요|다)[.!]?$|[.!?…]\s')


def available() -> bool:
    import api_status_view
    return bool(api_status_view.credentials().get('LINER_API_KEY'))


def hide_numbers(text: str) -> str:
    return NUMBERS.sub('○', text)


def question(text: str, screen: str) -> str | None:
    text = ' '.join(re.sub(r'[‘’“”"\']', '', hide_numbers(text)).split())[:LIMIT]
    if not text:
        return None
    if SENTENCE.search(text) or len(text) > 30:
        return f'이 내용을 쉽게 설명해 주세요: “{text}”' + (f' ({screen} 화면)' if screen else '')
    return f'‘{text}’의 뜻을 쉽게 알려 주세요.' + (f' ({screen} 화면)' if screen else '')


def known(text: str) -> tuple[str, str] | None:
    """(term, meaning) the screens' own glossary has for this text: the term itself, else the longest one inside it."""
    from liner_topics import glossary, normalized

    asked = normalized(text)
    terms = {normalized(term): (term, meaning) for term, meaning in glossary().items()}
    inside = [key for key in terms if len(key) > 2 and key in asked]
    key = asked if asked in terms else max(inside, key=len, default=None)
    return terms.get(key)


def fill(text: str, note: tuple[str, str] | None = None) -> None:
    """Open the panel with `text` in its input box; `note` is (title, what the service already says)."""
    st.session_state[BOX] = text
    st.session_state['ai_draft'] = note
    st.session_state['ai_float_open'] = True
    st.session_state['ai_float_page'] = '채팅'


def _redraw_panel() -> None:
    try:
        st.rerun('ai_float')  # from a widget callback: the panel alone, the screen behind stays
    except StreamlitAPIException:  # the panel is not on this page (tests, a screen without it)
        pass


def screen() -> str:
    return str(st.session_state.get('main_tab') or '')


def ask(text: str, meaning: str | None = None) -> None:
    """Widget callback: ask about a term or a card."""
    if wanted := question(text, screen()):
        fill(wanted, (text, meaning) if meaning else known(text))
        _redraw_panel()


def ask_question(wanted: str) -> None:
    """Widget callback: a question that is already written."""
    fill(wanted)
    _redraw_panel()


def _buttons(title: str, meanings: dict[str, str | None], key: str) -> None:
    """One small button per name. Buttons, not pills: a pill stays pressed and swallows the second click."""
    with st.container(horizontal=True, gap='xsmall', vertical_alignment='center', key=f'ask_row_{key}'):
        st.caption(title, width='content', help='누르면 서비스 설명을 먼저 보여 주고 오른쪽 아래 채팅창에 질문을 채웁니다. 보내기를 눌러야 전송됩니다.')
        for name, meaning in meanings.items():
            st.button(name, key=f'ask_{key}_{name}', type='tertiary', on_click=ask, args=(name, meaning))


def guide_questions(key: str) -> None:
    """Below a screen's guide: the whole screen, or one of its terms."""
    if not available():
        return
    from guides import GUIDES

    guide = GUIDES[key]
    st.button('이 화면을 AI에게 묻기', icon=':material/forum:', key=f'ask_screen_{key}', on_click=ask_question,
              args=(f"{guide['title']} 화면은 어떤 순서로 보면 되나요?",),
              help='오른쪽 아래 채팅창에 질문을 채웁니다. 보내기를 눌러야 전송됩니다.')
    if terms := guide.get('terms'):
        _buttons(':material/forum: 용어 묻기', terms, f'terms_{key}')


def card_questions(items: list[dict], key: str) -> None:
    """Below a row of status cards: ask about one (its own help text is shown first)."""
    if available():
        _buttons(':material/forum: AI에게 묻기', {item['label']: item.get('help') for item in items}, f'cards_{key}')


def ideas(tab: str) -> list[str]:
    """Three questions for the screen the user is on."""
    from guides import GUIDES

    terms = [term for guide in GUIDES.values() if guide['title'].split(' (')[0].split(' →')[0] == tab for term in guide.get('terms') or {}]
    first = f'{tab} 화면은 어떤 순서로 보면 되나요?' if tab else '이 서비스가 처음인데 대시보드에서 어떤 순서로 보면 되나요?'
    return [first, *(question(term, tab) for term in terms), f'{tab} 화면에서 처음 보는 사람이 자주 오해하는 것은 무엇인가요?',
            f'{tab} 화면의 숫자를 볼 때 무엇부터 확인해야 하나요?'][:3]


def idea_buttons() -> None:
    for index, wanted in enumerate(ideas(screen())):
        st.button(wanted, key=f'ask_idea_{index}', on_click=ask_question, args=(wanted,), type='tertiary', wrap=True)


def note() -> None:
    """In the panel: what the service already says about the drafted question."""
    if drafted := st.session_state.get('ai_draft'):
        from api_status_view import plain_symbols

        title, meaning = drafted
        st.info(plain_symbols(meaning), title=title, icon=':material/menu_book:')
        st.caption('서비스 설명입니다. 더 궁금하면 아래 질문을 고쳐 보내세요.')


# Text selected anywhere in the page gets a small button. The component runs in the page itself (not in a frame); it only
# reads the selection and tells the server when the button is pressed. Canvas tables and framed diagrams have no selection.
# The button hangs on <body> as a popover: an open pop-up makes the app's own root inert and would cover it.
_selection = st.components.v2.component(
    'ask_ai_selection',
    html='<span hidden></span>',
    js="""
export default function ({ parentElement, setTriggerValue }) {
    const theme = getComputedStyle(parentElement.querySelector('span'));
    const button = document.createElement('button');
    button.type = 'button';
    button.popover = 'manual';
    button.textContent = 'AI에게 묻기';
    button.title = '선택한 글을 채팅창에 질문으로 채웁니다. 보내기를 눌러야 전송됩니다.';
    button.style.cssText = `position:fixed;inset:auto;margin:0;padding:6px 14px;border-radius:999px;border:0;cursor:pointer;pointer-events:auto;
        background:${theme.getPropertyValue('--st-primary-color') || '#16a34a'};color:#fff;font:600 14px ${theme.getPropertyValue('--st-font') || 'sans-serif'};
        box-shadow:0 4px 14px rgba(2,6,23,.35)`;
    document.body.append(button);
    let text = '', card = '', timer = 0, down = false;
    const hide = () => { if (button.matches(':popover-open')) button.hidePopover(); };
    const show = () => {
        const selection = document.getSelection();
        text = selection ? selection.toString().trim().replace(/\\s+/g, ' ') : '';
        const node = selection && selection.anchorNode;
        const element = node && (node.nodeType === 1 ? node : node.parentElement);
        if (down || text.length < 2 || !element || !selection.rangeCount || element.closest('pre, code, input, textarea, [contenteditable]')) return hide();
        // "오늘 실현: +19,350원" is a status card's label; a number on its own means nothing without it
        card = (element.closest('[role="group"][aria-label]')?.getAttribute('aria-label') || '').split(':')[0];
        if (!card && !/[가-힣A-Za-z]{2,}/.test(text)) return hide();
        const box = selection.getRangeAt(0).getBoundingClientRect();
        if (!box.width && !box.height) return hide();
        button.inert = false;
        button.removeAttribute('aria-hidden');
        if (!button.matches(':popover-open')) button.showPopover();
        const width = button.offsetWidth, height = button.offsetHeight;
        button.style.left = `${Math.max(8, Math.min(box.right - width, innerWidth - width - 8))}px`;
        button.style.top = `${box.top > height + 16 ? box.top - height - 8 : Math.min(box.bottom + 8, innerHeight - height - 8)}px`;
    };
    const later = () => { clearTimeout(timer); timer = setTimeout(show, 250); };
    const press = (event) => { if (event.target !== button) { down = true; hide(); } };
    const release = (event) => { down = false; if (event.target !== button) later(); };
    const escape = (event) => { if (event.key === 'Escape') hide(); };
    const send = () => {
        hide();
        document.getSelection().removeAllRanges();
        setTriggerValue('picked', { text: text.slice(0, 200), card: card.slice(0, 80), dialog: !!document.querySelector('[role="dialog"]') });
    };
    // ours alone: an open pop-up must not take it as a press outside itself, and pressing must not clear the selection
    const own = (event) => {
        if (event.target !== button) return;
        event.stopImmediatePropagation();
        if (event.type === 'mousedown') event.preventDefault();
        if (event.type === 'click') send();
    };
    const events = [[document, 'selectionchange', later], [document, 'pointerdown', press, true], [document, 'pointerup', release, true],
                    [document, 'pointercancel', release, true], [document, 'keydown', escape, true], [document, 'scroll', hide, true],
                    ...['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].map((name) => [window, name, own, true])];
    for (const [target, name, handler, capture] of events) target.addEventListener(name, handler, capture);
    return () => {
        clearTimeout(timer);
        for (const [target, name, handler, capture] of events) target.removeEventListener(name, handler, capture);
        button.remove();
    };
}
""",
)


def picked() -> None:
    """Component callback: the selected text, and the status card it is in. A bare number is asked about by the card's name."""
    value = st.session_state.get('ask_selection') or {}
    value = value.get('picked') if hasattr(value, 'get') else None
    text, card = ((value.get('text'), value.get('card')) if hasattr(value, 'get') else (None, None))
    if not isinstance(text, str):
        return
    if isinstance(card, str) and card and card not in text and text not in card:
        text = f'{card}: {text}' if WORD.search(hide_numbers(text)) else card
    if not WORD.search(hide_numbers(text)):
        return
    if wanted := question(text, screen()):
        fill(wanted, known(text))
        if value.get('dialog'):  # the pop-up the text was in would cover the chat
            dialogs.close()
            st.session_state['ai_close_dialog'] = True


def selection_button() -> None:
    _selection(key='ask_selection', on_picked_change=picked, width='content', height='content')
