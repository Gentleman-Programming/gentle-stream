import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web/index.html"
COMMANDS = (
    "uv tool install 'gentle-stream[samples] @ git+https://github.com/"
    "Gentleman-Programming/gentle-stream'\ngentle-stream"
)


class LandingPage(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.text = []
        self.in_code = False
        self.commands = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "code" and attrs.get("id") == "install-commands":
            self.in_code = True

    def handle_endtag(self, tag):
        if tag == "code":
            self.in_code = False

    def handle_data(self, data):
        self.text.append(data)
        if self.in_code:
            self.commands.append(data)

    def attributes(self, element_id):
        return next((attrs for _, attrs in self.elements if attrs.get("id") == element_id), {})


class WebsiteTests(unittest.TestCase):
    def page(self):
        self.assertTrue(PAGE.is_file(), "The static landing page must exist at web/index.html")
        return LandingPage(PAGE.read_text(encoding="utf-8"))

    def test_installation_is_readable_before_javascript_runs(self):
        page = self.page()
        self.assertEqual("".join(page.commands), COMMANDS)
        self.assertTrue(page.attributes("install"))
        self.assertNotIn("hidden", page.attributes("install-commands"))
        self.assertIn("hidden", page.attributes("copy-commands"))
        self.assertEqual(page.attributes("copy-status").get("role"), "status")
        visible_copy = " ".join(" ".join(page.text).split())
        for requirement in ("macOS or Linux", "uv", "gentle-shell", "PATH", "signed-in", "audio output device", "PortAudio", "libportaudio2", "FluidSynth"):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, visible_copy)

    def test_player_navigation_and_local_assets(self):
        page = self.page()
        links = [attrs["href"] for tag, attrs in page.elements if tag == "a"]
        self.assertIn("#player", links)
        self.assertIn("#install", links)
        self.assertTrue(page.attributes("player"))
        images = [attrs for tag, attrs in page.elements if tag == "img"]
        self.assertEqual([img.get("src") for img in images], ["../docs/assets/tui.svg"])
        self.assertIn("Café Rain", images[0].get("alt", ""))
        assets = [attrs["src"] for _, attrs in page.elements if "src" in attrs]
        assets += [attrs["href"] for tag, attrs in page.elements if tag == "link"]
        self.assertCountEqual(assets, ["../docs/assets/tui.svg", "styles.css", "install.js"])
        for asset in assets:
            with self.subTest(asset=asset):
                self.assertEqual(urlsplit(asset).netloc, "")
                self.assertEqual(urlsplit(asset).scheme, "")
                self.assertTrue((PAGE.parent / asset).is_file())

    @unittest.skipUnless(shutil.which("node"), "Node is only needed to exercise the browser script")
    def test_copy_reports_actual_clipboard_outcomes(self):
        page = self.page()
        script = next(attrs["src"] for tag, attrs in page.elements if tag == "script")
        result = subprocess.run(
            ["node", "-", str(PAGE.parent / script), "".join(page.commands)],
            input="""
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');
const [, , script, text] = process.argv;
const source = readFileSync(script, 'utf8');

function load(clipboard) {
  const button = { hidden: true, addEventListener(type, callback) {
    assert.equal(type, 'click');
    this.click = callback;
  } };
  const commands = { textContent: text };
  const status = { textContent: '' };
  const elements = { 'install-commands': commands, 'copy-commands': button, 'copy-status': status };
  runInNewContext(source, { document: { getElementById: id => elements[id] }, navigator: { clipboard } });
  assert.equal(button.hidden, false);
  return { button, commands, status };
}

(async () => {
  let finish;
  const success = load({ writeText(value) {
    assert.equal(value, text);
    return new Promise(resolve => { finish = resolve; });
  } });
  const pending = success.button.click();
  assert.equal(success.status.textContent, '');
  finish();
  await pending;
  assert.equal(success.status.textContent, 'Commands copied.');
  assert.equal(success.commands.textContent, text);

  for (const clipboard of [undefined, { writeText: () => Promise.reject(Error('Denied')) }]) {
    const failure = load(clipboard);
    await failure.button.click();
    assert.equal(failure.status.textContent, 'Could not copy. Select and copy the commands manually.');
    assert.equal(failure.commands.textContent, text);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""), result.stderr)
