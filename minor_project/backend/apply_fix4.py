import os, shutil

def read(p):
    with open(p) as f:
        return f.read()

if not (os.path.exists("App.jsx") and os.path.exists("App.css")):
    raise SystemExit("Run this in the folder that contains App.jsx and App.css")

jsx = read("App.jsx")
if "decision-failed" in jsx:
    raise SystemExit("already applied")

old_open = '                <p>\n                  {result.stopped_early'
old_close = ('                  This is an AI-generated proposal; human review is required before real-world use.\n'
             '                </p>\n')
for text in (old_open, old_close):
    if text not in jsx:
        raise SystemExit("could not find the decision card text in App.jsx - send this message to Claude")

jsx = jsx.replace(old_open, '                <p className="decision-text">\n                  {result.stopped_early', 1)
jsx = jsx.replace(old_close, old_close + '''                {checks.some((c) => !c.passed) && (
                  <p className="decision-failed">
                    Failed checks:{" "}
                    {checks
                      .filter((c) => !c.passed)
                      .map((c) => c.name || c.check)
                      .join(" · ")}
                  </p>
                )}
''', 1)

shutil.copy("App.jsx", "App.jsx.bak4")
with open("App.jsx", "w") as f:
    f.write(jsx)

with open("App.css", "a") as f:
    f.write('''

/* decision card: failed-check summary */
.decision-card .decision-text { margin: 0 0 6px; font-size: 13px; line-height: 1.7; max-width: 640px; }
.decision-card .decision-failed { margin: 8px 0 0; font-size: 12px; font-weight: 700; line-height: 1.6; max-width: 640px; }
''')
print("done (backup: App.jsx.bak4)")
