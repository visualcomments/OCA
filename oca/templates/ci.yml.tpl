name: Course checks

# ШАБЛОН CI для репозитория курса.
# Проверяет то, что реально проверяемо: ссылки, ноутбуки, верификацию цитат.
# НЕ запускает тяжёлые сборки и не требует секретов.

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  course-checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          pip install nbformat nbconvert

      - name: Validate notebooks parse and are clean
        run: |
          python - <<'PY'
          import json, pathlib, sys
          bad = []
          for p in pathlib.Path(".").rglob("*.ipynb"):
              if ".ipynb_checkpoints" in str(p):
                  continue
              try:
                  nb = json.loads(p.read_text(encoding="utf-8"))
              except Exception as e:
                  bad.append(f"{p}: не парсится ({e})")
                  continue
              for i, c in enumerate(nb.get("cells", [])):
                  if c.get("cell_type") == "code":
                      for o in c.get("outputs", []):
                          if o.get("output_type") == "error":
                              bad.append(f"{p}: ячейка {i} содержит ошибку выполнения")
          if bad:
              print("\n".join(bad)); sys.exit(1)
          print("Ноутбуки чистые")
          PY

      - name: Check relative links in Markdown
        run: |
          python - <<'PY'
          import pathlib, re, sys
          link = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
          bad = []
          for p in pathlib.Path(".").rglob("*.md"):
              if ".git" in p.parts:
                  continue
              for target in link.findall(p.read_text(encoding="utf-8", errors="replace")):
                  if target.startswith(("http://", "https://", "mailto:", "#")):
                      continue
                  t = target.split("#", 1)[0]
                  if t and not (p.parent / t).exists():
                      bad.append(f"{p} → {target}")
          if bad:
              print("Битые ссылки:"); print("\n".join(bad)); sys.exit(1)
          print("Ссылки в порядке")
          PY

      - name: Verify course materials (if present)
        run: |
          if [ -f Makefile ] && grep -qE '^verify:' Makefile; then
            make verify
          else
            echo "Цель verify не найдена — пропуск"
          fi
