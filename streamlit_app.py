"""쇼핑리스트 Streamlit 앱 (PRD: prd.md)

웹 버전(index.html / app.js)과 같은 기능을 Streamlit으로 구현한다.
브라우저 localStorage 대신 같은 폴더의 shopping_list.json 파일에 저장한다.

실행: streamlit run streamlit_app.py
"""

import html
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

DATA_FILE = Path(__file__).with_name("shopping_list.json")
MAX_NAME_LENGTH = 50
FILTERS = ["전체", "미완료", "완료"]
EMPTY_MESSAGES = {
    "전체": "쇼핑리스트가 비어 있어요. 살 물건을 추가해 보세요!",
    "미완료": "남은 항목이 없어요.",
    "완료": "완료한 항목이 없어요.",
}

st.set_page_config(page_title="쇼핑리스트", page_icon="🛒", layout="centered")


# ---------- 데이터 저장 (F-05) ----------

@st.cache_resource
def get_file_lock():
    """모든 세션(탭)이 공유하는 잠금. 읽기/쓰기가 겹쳐 서로 덮어쓰거나
    Windows에서 열려 있는 파일을 교체하지 못하는 문제를 막는다."""
    return threading.RLock()


def is_valid_item(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"] != ""
        and isinstance(item.get("name"), str)
        and item["name"].strip() != ""
        and isinstance(item.get("checked"), bool)
    )


def load_items():
    """파일에서 목록을 읽는다. 파일이 없거나 손상되었으면 빈 목록."""
    try:
        with get_file_lock():
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    # 같은 id가 여러 번 있으면 위젯 key가 겹쳐 앱이 멈추므로 첫 번째만 사용
    seen = set()
    items = []
    for item in data:
        if is_valid_item(item) and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    return items


def save_items(items):
    """임시 파일에 쓴 뒤 교체해서 저장 중 손상을 막는다."""
    tmp = DATA_FILE.with_name(f"{DATA_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    # 다른 프로그램(편집기, 백신 등)이 잠깐 파일을 잡고 있으면 Windows에서 교체가 실패하므로 재시도
    for attempt in range(20):
        try:
            tmp.replace(DATA_FILE)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def update_items(change):
    """읽기 → 변경 → 저장을 잠금 안에서 한 번에 처리한다."""
    with get_file_lock():
        items = change(load_items())
        save_items(items)
        return items


def normalize_name(name):
    # 글자(코드 포인트) 단위로 잘라서 이모지가 깨지지 않게 함
    return (name or "").strip()[:MAX_NAME_LENGTH]


def escape_md(text):
    """사용자 입력이 마크다운으로 해석되지 않도록 이스케이프 (툴팁용)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>$&:])", r"\\\1", text)


def plain_text(text, class_name=""):
    """사용자 입력을 마크다운 없이 글자 그대로 보여준다 (자동 링크도 막음)."""
    st.html(f'<p class="{class_name}">{html.escape(text)}</p>')


# ---------- 상태 변경 (콜백) ----------

def add_item():
    name = normalize_name(st.session_state.new_item)
    if not name:
        st.toast("살 물건 이름을 입력해 주세요.", icon="✏️")
        return
    new_item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "checked": False,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    update_items(lambda items: items + [new_item])
    # 완료 필터에서는 새 아이템이 보이지 않으므로 전체로 전환
    if st.session_state.filter == "완료":
        st.session_state.filter = "전체"


def toggle_item(item_id):
    checked = st.session_state[f"chk_{item_id}"]

    def change(items):
        for item in items:
            if item["id"] == item_id:
                item["checked"] = checked
        return items

    update_items(change)


def start_edit(item_id, name):
    # 한 번에 하나만 편집 (다른 편집은 자동으로 취소됨)
    st.session_state.editing_id = item_id
    st.session_state.edit_input = name


def save_edit():
    item_id = st.session_state.editing_id
    name = normalize_name(st.session_state.edit_input)
    if name:  # 빈 값이면 원래 이름 유지

        def change(items):
            for item in items:
                if item["id"] == item_id:
                    item["name"] = name
            return items

        update_items(change)
    st.session_state.editing_id = None


def cancel_edit():
    st.session_state.editing_id = None


def delete_item(item_id):
    update_items(lambda items: [item for item in items if item["id"] != item_id])
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None


def clear_completed():
    items = update_items(lambda items: [item for item in items if not item["checked"]])
    if not any(item["id"] == st.session_state.editing_id for item in items):
        st.session_state.editing_id = None


@st.dialog("항목 삭제")
def confirm_delete(item):
    plain_text(f"\"{item['name']}\" 항목을 삭제할까요?")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("취소", key="dlg_cancel"):
            st.rerun()
        if st.button("삭제", key="dlg_delete", type="primary"):
            delete_item(item["id"])
            st.rerun()



# ---------- 화면 ----------

st.session_state.setdefault("editing_id", None)
st.session_state.setdefault("filter", "전체")

st.html("""
<style>
  .block-container { max-width: 560px; padding-top: 3rem; }
  h1 { font-weight: 800; letter-spacing: -0.02em; }
  [class*="st-key-row_"],
  [class*="st-key-edit_row_"] {
    border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    padding: 4px 0;
    min-height: 48px;
  }
  [class*="st-key-name_"] p {
    margin: 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: keep-all;
    cursor: text;
  }
  /* 체크된 아이템: 취소선 + 흐린 색 */
  [class*="st-key-row_"]:has(input[type="checkbox"]:checked) [class*="st-key-name_"] p {
    text-decoration: line-through;
    text-decoration-thickness: 2px;
    opacity: 0.7; /* 명도 대비 4.5:1 이상 유지 */
  }
  /* 삭제 계열 버튼은 빨간색 */
  [class*="st-key-del_"] button p,
  .st-key-clear_completed button:not(:disabled) p { color: #d93636; }
  .st-key-clear_completed button p,
  [class*="st-key-edit_btn_"] button p { font-weight: 600; }
  /* 체크박스 테두리: 기본값(0.67px, 20% 회색)은 거의 보이지 않아 진하게 */
  [class*="st-key-row_"] label[data-baseweb="checkbox"] > span:first-child {
    width: 20px;
    height: 20px;
    border-width: 2px !important;
  }
  [class*="st-key-row_"] label[data-baseweb="checkbox"]:has(input:not(:checked)) > span:first-child {
    border-color: #7d828c !important;
  }
  /* 기본 caption 회색은 대비가 부족함 */
  [data-testid="stCaptionContainer"] { color: inherit; opacity: 0.8; }
  .empty-message { text-align: center; opacity: 0.75; padding: 32px 0; }
  /* 다크 모드: 밝은 초록 위 흰 글자/어두운 배경 위 빨간 글자의 대비 보정 */
  @media (prefers-color-scheme: dark) {
    [data-testid="stBaseButton-primary"] p,
    [data-testid="stBaseButton-primaryFormSubmit"] p { color: #0b1f12; }
    [class*="st-key-del_"] button p,
    .st-key-clear_completed button:not(:disabled) p { color: #f87171; }
  }
  /* 휴대폰에서 버튼/체크박스 터치 영역 44px 이상 */
  @media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] button { min-height: 44px; min-width: 44px; }
    [data-testid="stMainBlockContainer"] input[type="text"] { min-height: 44px; }
    [class*="st-key-row_"] label[data-baseweb="checkbox"] { min-height: 44px; min-width: 44px; align-items: center; justify-content: center; }
  }
</style>
""")

# Streamlit 기본 기능으로 안 되는 부분을 브라우저 쪽에서 보완
# - 50자 초과 붙여넣기 시 입력 전체가 무시되는 문제 → 브라우저 maxlength로 잘라내기
# - 추가 후 입력창 포커스 유지, 편집 시작 시 편집창 자동 포커스
# - 편집 중 Esc로 취소, 이름 더블클릭으로 편집 시작
st.html(
    """
<script>
(() => {
  if (window.__shoppingEnhanced) return;
  window.__shoppingEnhanced = true;

  const MAX = %d;
  const ADD = 'input[placeholder="살 물건을 입력하세요..."]';
  const EDIT_ROW = '[class*="st-key-edit_row_"]';
  const EDIT = EDIT_ROW + ' input[type="text"]';
  let refocusAdd = false;
  let focusedEditRow = null;
  let refocusEditButton = null; // 편집을 끝낸 항목 id → 그 항목의 [수정] 버튼으로 포커스
  const idOf = (el) => {
    const row = el && el.closest(EDIT_ROW);
    const cls = row && [...row.classList].find((c) => c.startsWith("st-key-edit_row_"));
    return cls ? cls.slice("st-key-edit_row_".length) : null;
  };

  const visibleButton = (scope, text) =>
    [...scope.querySelectorAll("button")].find((b) => b.innerText.trim() === text && b.offsetParent !== null);

  const apply = () => {
    document.querySelectorAll(ADD + ", " + EDIT).forEach((el) => {
      if (el.maxLength !== MAX) el.maxLength = MAX;
    });
    // 화면 읽기 프로그램용: "수정" → "계란 수정"처럼 항목 이름을 붙임
    document.querySelectorAll('[class*="st-key-row_"]').forEach((row) => {
      const name = row.querySelector('[class*="st-key-name_"] p');
      if (!name) return;
      row.querySelectorAll("button").forEach((b) => {
        const label = name.textContent + " " + b.innerText.trim();
        if (b.getAttribute("aria-label") !== label) b.setAttribute("aria-label", label);
      });
    });
    const add = document.querySelector(ADD);
    if (refocusAdd && add) {
      refocusAdd = false;
      add.focus();
    }
    // 편집창은 새 항목 편집이 시작될 때만 한 번 포커스 (다른 조작 중 포커스를 빼앗지 않음)
    const edit = document.querySelector(EDIT);
    const rowKey = edit ? [...edit.closest(EDIT_ROW).classList].find((c) => c.startsWith("st-key-edit_row_")) : null;
    if (edit && rowKey !== focusedEditRow) {
      focusedEditRow = rowKey;
      edit.focus();
      edit.setSelectionRange(edit.value.length, edit.value.length);
    }
    if (!edit) focusedEditRow = null;
    if (refocusEditButton && !edit) {
      const container = document.querySelector(".st-key-edit_btn_" + CSS.escape(refocusEditButton));
      const button = container && visibleButton(container, "수정");
      if (button) {
        refocusEditButton = null;
        button.focus();
      }
    }
  };

  new MutationObserver(apply).observe(document.body, { childList: true, subtree: true });

  document.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.key === "Enter" && e.target.matches(ADD)) refocusAdd = true;
    if ((e.key === "Enter" || e.key === "Escape") && e.target.closest(EDIT_ROW)) refocusEditButton = idOf(e.target);
    if (e.key === "Escape" && e.target.matches(EDIT)) {
      const cancel = visibleButton(e.target.closest(EDIT_ROW), "취소");
      if (cancel) {
        e.preventDefault();
        e.stopPropagation();
        cancel.click();
      }
    }
  }, true);

  document.addEventListener("click", (e) => {
    if (e.target.closest(".st-key-add_btn")) refocusAdd = true;
    if (e.target.closest(EDIT_ROW + " button")) refocusEditButton = idOf(e.target);
  }, true);

  document.addEventListener("dblclick", (e) => {
    const name = e.target.closest('[class*="st-key-name_"]');
    const row = name && name.closest('[class*="st-key-row_"]');
    const edit = row && visibleButton(row, "수정");
    if (edit) edit.click();
  });

  apply();
})();
</script>
"""
    % MAX_NAME_LENGTH,
    unsafe_allow_javascript=True,
)

st.title("🛒 쇼핑리스트", anchor=False)

# F-01 아이템 추가 (Enter 또는 추가 버튼)
with st.form("add_form", clear_on_submit=True, border=False):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.text_input(
            "살 물건",
            key="new_item",
            placeholder="살 물건을 입력하세요...",
            label_visibility="collapsed",
            width="stretch",
        )
        st.form_submit_button("추가", type="primary", on_click=add_item, key="add_btn")

# F-06 필터
current_filter = st.segmented_control(
    "목록 필터",
    FILTERS,
    key="filter",
    label_visibility="collapsed",
    width="stretch",
) or "전체"

items = load_items()
if current_filter == "미완료":
    visible = [item for item in items if not item["checked"]]
elif current_filter == "완료":
    visible = [item for item in items if item["checked"]]
else:
    visible = items

# 다른 세션(탭)에서 바뀐 체크 상태만 위젯에 반영
# (값이 같을 때 덮어쓰면 실행 중에 누른 클릭이 서버 값으로 되돌려짐)
for item in visible:
    key = f"chk_{item['id']}"
    if st.session_state.get(key) != item["checked"]:
        st.session_state[key] = item["checked"]

# F-02 ~ F-04 아이템 목록
for item in visible:
    item_id = item["id"]

    if item_id == st.session_state.editing_id:
        with st.form(f"edit_form_{item_id}", border=False):
            with st.container(horizontal=True, vertical_alignment="center", key=f"edit_row_{item_id}"):
                st.text_input(
                    f"{item['name']} 새 이름",
                    key="edit_input",
                    label_visibility="collapsed",
                    width="stretch",
                )
                st.form_submit_button("저장", type="primary", on_click=save_edit)
                st.form_submit_button("취소", on_click=cancel_edit)
        continue

    # 행 key를 체크 여부와 무관하게 고정해서 체크해도 행이 다시 만들어지지 않게 함
    with st.container(horizontal=True, vertical_alignment="center", key=f"row_{item_id}"):
        st.checkbox(
            f"{item['name']} 구매 완료",
            key=f"chk_{item_id}",
            on_change=toggle_item,
            args=(item_id,),
            label_visibility="collapsed",
            width="content",
        )
        with st.container(key=f"name_{item_id}", width="stretch"):
            plain_text(item["name"])
        st.button(
            "수정",
            key=f"edit_btn_{item_id}",
            type="tertiary",
            on_click=start_edit,
            args=(item_id, item["name"]),
            help=f"{escape_md(item['name'])} 수정",
        )
        if st.button("삭제", key=f"del_{item_id}", type="tertiary", help=f"{escape_md(item['name'])} 삭제"):
            confirm_delete(item)

if not visible:
    message = EMPTY_MESSAGES["전체"] if not items else EMPTY_MESSAGES[current_filter]
    st.html(f'<p class="empty-message">{message}</p>')

# F-06 남은 개수 / 완료 항목 일괄 삭제
remaining = sum(1 for item in items if not item["checked"])
with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
    st.caption(f"**{remaining} / {len(items)}개 남음**")
    st.button(
        "완료 항목 삭제",
        key="clear_completed",
        type="tertiary",
        on_click=clear_completed,
        disabled=all(not item["checked"] for item in items),
    )
