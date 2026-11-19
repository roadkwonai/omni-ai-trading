#!/bin/zsh
# Build the landing page and place it at the root of the report Pages site, then let report_pages.py commit/push.
# Run only after the user approves the deploy (public repo + Pages + REPORT_PAGES_PUSH=on) and after restarting
# com.vibetrading.discord-notify, so the running notifier writes its archive to reports/ instead of the root.
set -euo pipefail
HERE=${0:A:h}/..
REPO=$HERE/..
SITE=${REPORT_PAGES_SITE:-$HOME/.vibe-trading/report-pages/site}
[[ -d $SITE ]] || { echo "리포트 사이트 폴더가 없습니다: $SITE" >&2; exit 1; }
pnpm --dir "$HERE" build
# reports/ and assets/ belong to report_pages.py; .git is the Pages checkout.
rsync -a --delete --exclude reports/ --exclude assets/ --exclude .git/ --exclude .nojekyll --exclude robots.txt "$HERE/dist/" "$SITE/"
rm -f "$SITE/reports.json"  # the archive data moved to reports/reports.json (2026-11-19)
printf 'User-agent: *\nDisallow: /reports/\n\nSitemap: https://roadkwonai.github.io/omni-ai-trading/sitemap.xml\n' > "$SITE/robots.txt"
printf '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://roadkwonai.github.io/omni-ai-trading/</loc></url></urlset>\n' > "$SITE/sitemap.xml"
# report_pages.py --publish pushes only when a report is waiting, so the landing pushes the site itself.
cd "$REPO" && PYTHONPATH=agent:scripts .venv/bin/python scripts/release.py --push-site
