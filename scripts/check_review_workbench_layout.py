"""Check the workbench scroll boundaries in headless Chrome without extra packages."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
PAGE = ROOT / "src/genesis_evidence/review/workbench.html"


def _fixture(source: str) -> str:
    probe = """
<script>
const queue = document.getElementById('queue');
for (let i = 0; i < 80; i += 1) {
  const button = document.createElement('button');
  button.innerHTML = `<strong>Long paper ${i}</strong><br><span class="muted">pending</span>`;
  queue.appendChild(button);
}
document.getElementById('detail').innerHTML =
  '<div class="panel" style="height:1800px"><h2>Long review</h2></div>';
setTimeout(() => {
  const style = selector => getComputedStyle(document.querySelector(selector));
  const aside = document.querySelector('aside');
  const content = document.querySelector('.content');
  const before = aside.scrollTop;
  content.scrollTop = 120;
  document.querySelector('[data-view="disease"]').click();
  const diseaseShown = !document.getElementById('diseaseView').hidden;
  document.querySelector('[data-view="review"]').click();
  const reviewShown = !document.getElementById('reviewView').hidden;
  const result = {
    width: innerWidth,
    bodyDisplay: getComputedStyle(document.body).display,
    bodyOverflow: getComputedStyle(document.body).overflow,
    bodyScrollWidth: document.body.scrollWidth,
    bodyClientWidth: document.body.clientWidth,
    mainOverflow: style('main').overflow,
    asideOverflowY: style('aside').overflowY,
    contentOverflowY: style('.content').overflowY,
    asideCanScroll: aside.scrollHeight > aside.clientHeight,
    contentCanScroll: content.scrollHeight > content.clientHeight,
    contentScrolled: content.scrollTop > 0,
    asideUnchanged: aside.scrollTop === before,
    windowUnchanged: scrollY === 0,
    diseaseShown,
    reviewShown,
  };
  const output = document.createElement('pre');
  output.id = 'layout-check-result';
  output.textContent = JSON.stringify(result);
  document.body.appendChild(output);
}, 100);
</script>
"""
    return source.replace("</body>", probe + "</body>", 1)


def _run(width: int, height: int) -> dict[str, object]:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("google-chrome or chromium is required")
    with tempfile.TemporaryDirectory(prefix="review-workbench-layout-") as directory:
        page = Path(directory) / "workbench.html"
        page.write_text(_fixture(PAGE.read_text(encoding="utf-8")), encoding="utf-8")
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                f"--window-size={width},{height}",
                "--virtual-time-budget=1000",
                "--dump-dom",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    marker = '<pre id="layout-check-result">'
    start = result.stdout.find(marker)
    if start < 0:
        raise AssertionError("layout probe did not run")
    payload = result.stdout[start + len(marker) :].split("</pre>", 1)[0]
    return json.loads(payload)


def main() -> None:
    desktop = _run(1440, 900)
    assert desktop["bodyDisplay"] == "flex"
    assert desktop["mainOverflow"] == "hidden"
    assert desktop["asideOverflowY"] in {"auto", "scroll"}
    assert desktop["contentOverflowY"] in {"auto", "scroll"}
    assert desktop["asideCanScroll"] and desktop["contentCanScroll"]
    assert desktop["contentScrolled"] and desktop["asideUnchanged"] and desktop["windowUnchanged"]
    assert desktop["diseaseShown"] and desktop["reviewShown"]
    mobile = _run(390, 844)
    assert mobile["bodyDisplay"] == "block"
    assert mobile["mainOverflow"] == "visible"
    assert mobile["bodyScrollWidth"] == mobile["bodyClientWidth"]
    print(json.dumps({"desktop": desktop, "mobile": mobile}, ensure_ascii=False))


if __name__ == "__main__":
    main()
