"""백업·이식 tab (2026-11-17): state backup archives, download/upload, restore and the new-account check.

Everything goes through scripts/state_sync.py. Import is offered only while no trading service runs and needs the typed phrase.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import state_sync as ss

PHRASE = '백업 가져오기'


def _archives():
    out = []
    for path in ss.archives(ss.STATE / 'backups'):
        stat = path.stat()
        out.append({'파일': path.name, '크기(MB)': round(stat.st_size / 1e6, 1),
                    '시각': datetime.fromtimestamp(stat.st_mtime, ss.KST).strftime('%Y-%m-%d %H:%M')})
    return out


def render(root=None):
    root = ss.runtime_root() if root is None else Path(root)
    rows = _archives()
    st.caption(f'실행 데이터 위치: {root}. 설정은 git(state/settings, 계좌번호 제거본), 원장·이벤트·리포트는 아래 백업 파일로 옮깁니다.')
    top = st.columns([3, 1])
    top[0].metric('마지막 백업', rows[0]['시각'] if rows else '없음', f'{len(rows)}개 보관 (최근 {ss.KEEP}개)')
    if top[1].button('지금 백업', key='backup_now', type='primary'):
        with st.spinner('백업 만드는 중 (수 분 걸릴 수 있습니다)'):
            try:
                written = ss.export_settings(root, ss.STATE / 'settings')
                archive, manifest = ss.export_archive(root, ss.STATE / 'backups')
                st.success(f'{archive.name} ({archive.stat().st_size / 1e6:.1f}MB, 파일 {len(manifest["files"])}개, 설정 {len(written)}개)')
            except Exception as exc:  # noqa: BLE001 - show, never crash the dashboard
                st.error(f'백업 실패: {type(exc).__name__}')
        rows = _archives()

    st.subheader('백업 파일')
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        pick = st.selectbox('내려받기', [r['파일'] for r in rows], key='backup_pick')
        with open(ss.STATE / 'backups' / pick, 'rb') as stream:
            st.download_button('선택한 백업 내려받기', stream, file_name=pick, mime='application/gzip', key='backup_dl')
    else:
        st.info('백업이 아직 없습니다. 평일 16:55 자동 백업 또는 위 버튼으로 만듭니다.')

    st.subheader('가져오기')
    running = ss.running_services()
    if running is None:
        st.warning('서비스 상태를 확인할 수 없어 가져올 수 없습니다.')
    elif running:
        st.warning('거래 서비스가 실행 중이라 가져올 수 없습니다: ' + ', '.join(r.removeprefix('com.vibetrading.') for r in running))
    upload = st.file_uploader('백업 파일(.tar.gz)', type=['gz'], key='backup_up')
    if upload is not None:
        tmp = Path(tempfile.mkdtemp(prefix='state-upload-')) / 'upload.tar.gz'
        tmp.write_bytes(upload.getbuffer())
        problems = ss.verify(tmp)
        if problems:
            st.error('검증 실패: ' + '; '.join(problems[:5]))
        else:
            manifest = ss.read_manifest(tmp)
            st.success(f'검증 통과: 파일 {len(manifest["files"])}개, 만든 시각 {manifest["created"]}, 커밋 {manifest.get("git_commit", "")[:8]}')
            typed = st.text_input(f"가져오려면 '{PHRASE}'를 입력하세요. 덮어쓸 파일은 restore-backup-시각 폴더로 옮겨 둡니다.", key='backup_phrase')
            if st.button('가져오기', key='backup_import', disabled=running is None or bool(running) or typed.strip() != PHRASE):
                try:
                    result = ss.import_archive(tmp, root)
                    st.success(f'복원 {result["restored"]}개, 기존 {result["moved_aside"]}개는 {result["aside"] or "(없음)"}에 보관')
                except SystemExit as exc:
                    st.error(str(exc))

    st.subheader('새 계정·새 기기 점검')
    info = ss.check(root)
    table = [
        ('실행 데이터 폴더', '있음' if info['runtime_exists'] else '없음 (백업에서 복원 필요)'),
        ('.env 키(이름만)', f'{len(info["env_present"])}개 채움' + (f', 비어 있음 {len(info["env_empty"])}' if info['env_empty'] else '')
         + (f', 예시에만 있음: {", ".join(info["env_missing_vs_example"])}' if info['env_missing_vs_example'] else '')),
        ('서비스', ', '.join(f'{k}:{v}' for k, v in info['services'].items()) or '-'),
        ('예약 작업 부족', ', '.join(info['tasks_missing']) or '없음'),
        ('마지막 백업', f'{info["latest_backup"]} ({info["latest_backup_age_h"]}시간 전)' if info['latest_backup'] else '없음'),
    ]
    st.dataframe(pd.DataFrame(table, columns=['항목', '상태']), hide_index=True, width='stretch')
