"""Copy a Markdown response from the AI search panel."""
import streamlit as st

copy_markdown = st.components.v2.component(
    'ai_search_markdown_clipboard',
    html="""<button type="button" title="가장 최근 AI 답변의 본문과 웹 출처, 서비스 가이드를 마크다운으로 클립보드에 복사합니다."
 aria-description="가장 최근 AI 답변과 출처를 마크다운으로 클립보드에 복사합니다.">마크다운 복사</button>
<span role="status"></span><textarea aria-label="복사할 마크다운" hidden readonly></textarea>""",
    css="""
button {
    color: var(--st-text-color);
    background: var(--st-secondary-background-color);
    border: 1px solid var(--st-border-color);
    border-radius: .5rem;
    font: inherit;
    padding: .35rem .7rem;
    cursor: pointer;
}
textarea { display: block; width: 100%; color: inherit; background: inherit; }
textarea[hidden] { display: none; }
[role="status"] { margin-inline-start: .5rem; }
""",
    js="""
export default function ({data, parentElement}) {
    const button = parentElement.querySelector('button');
    const status = parentElement.querySelector('[role="status"]');
    const fallback = parentElement.querySelector('textarea');
    if (data.label) {
        button.textContent = data.label;
        button.title = '';
        button.removeAttribute('aria-description');
    }
    button.onclick = async () => {
        try {
            await navigator.clipboard.writeText(data.markdown);
            fallback.hidden = true;
            status.textContent = '복사했습니다.';
        } catch {
            status.textContent = '선택된 내용을 키보드로 복사하세요.';
            fallback.value = data.markdown;
            fallback.hidden = false;
            fallback.focus();
            fallback.select();
        }
    };
}
""",
)
