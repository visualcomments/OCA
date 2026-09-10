---
name: oca-course-publish
description: OCA (Open Course Advisor) — публикация улучшений курса как ветки/коммита/pull request, аналог git-агента OSA. Готовит осмысленные коммиты, ветку, PR с описанием находок. Trigger on "опубликуй улучшения", "сделай PR", "запушь курс", "открой пулл-реквест", "create pull request for course", "закоммить правки курса". Requires the gh CLI and a GitHub token.
whenToUse: После oca-course-improve, когда улучшения готовы локально и их нужно опубликовать в удалённый репозиторий курса.
---

# OCA — публикация улучшений курса

Аналог `GitAgent` из OSA: клонирование → ветка → коммит → push → PR.
OSA делает это автоматически; здесь ты делаешь это осознанно и с проверками.

## Жёсткие правила

1. **Никогда не коммить в основную ветку.** Всегда отдельная ветка
   `oca/improve-<YYYY-MM-DD>` или `oca/<тема>`.
2. **Не коммить секреты.** Перед коммитом проверь `.env`, токены, ключи,
   персональные данные. Нашёл — стоп.
3. **Не пушить без явного согласия человека.** Показать `git diff --stat`
   и дождаться «да».
4. **Один логический коммит — одно изменение.** Не сваливать 20 правок в один.
5. **Не переписывать историю.** Никаких `push --force` в чужие репозитории.
6. **Атрибуция обязательна.** В PR укажи, что изменения подготовлены OCA
   (аналог бейджа «improved by OSA»).

## Проверка окружения

```bash
export PATH="$HOME/.local/bin:$PATH"
gh --version
gh auth status || gh auth login   # интерактивно, device flow
```

Если токена нет — не пытайся писать в GitHub. Подготовь коммиты локально
и отдай человеку патч:

```bash
git format-patch -o /tmp/oca-patches main
```

## Порядок работы

### 1. Проверить рабочее дерево

```bash
git status --porcelain
git branch --show-current
git remote -v
```

Незнакомые изменения, которые ты не делал → спроси, прежде чем трогать.

### 2. Ветка

```bash
git checkout -b oca/improve-$(date +%Y-%m-%d)
```

### 3. Проверка на секреты и мусор

```bash
git status --porcelain | awk '{print $2}' | while read -r f; do
  grep -lEi '(api[_-]?key|secret|password|token)[[:space:]]*[:=]' "$f" 2>/dev/null
done
```

Отдельно проверь, что не добавляются: `.env`, `*.key`, `*.pem`,
`id_rsa*`, большие бинарники, датасеты, `.ipynb_checkpoints`.

### 4. Коммиты

```bash
git add <конкретные файлы>
git commit -m "$(cat <<'EOF'
docs(oca): add course license and content attribution

- add LICENSE (MIT) for code and LICENSE-CONTENT (CC BY-SA 4.0)
- add NOTICE with source attribution

Findings: OCA-002 (no-license), OCA-005 (no-content-license)
EOF
)"
```

Типы: `docs`, `feat`, `fix`, `chore`, `ci`.
В теле — коды находок из `oca-report.md`, чтобы PR был трассируемым.

### 5. Push

```bash
git push -u origin oca/improve-$(date +%Y-%m-%d)
```

### 6. Pull request

```bash
gh pr create --title "OCA: улучшение курса <название>" --body "$(cat <<'EOF'
## Что сделано

<список изменений>

## Находки, которые закрывает

| Код | Уровень | Было | Стало |
|---|---|---|---|

## Проверка

- `oca_scan.py` до: overall=X.XX
- `oca_scan.py` после: overall=Y.YY
- Блокеры: N → 0

## Что НЕ менялось

Содержание занятий не редактировалось.

---
Подготовлено [OCA](https://github.com/aimclub/OSA)-совместимым аудитом.
EOF
)"
```

### 7. Отчёт

Дай человеку: ссылку на PR, список коммитов, дельту оценок,
и явно перечисли то, что осталось незакрытым.

## Если репозиторий чужой (fork-flow)

```bash
gh repo fork --remote --remote-name fork
git push -u fork oca/improve-$(date +%Y-%m-%d)
gh pr create --repo <owner>/<repo> --head <your-user>:oca/improve-... 
```

Публикация в чужой репозиторий курса — это внешнее действие.
Всегда спрашивай подтверждение перед созданием PR.

## Антипаттерны

- `git add -A` вслепую — утащит `.env` и мусор.
- Один коммит «improve course» на 20 файлов.
- PR без указания, какие находки закрыты.
- Push в `main` «потому что так проще».
- Создать PR в upstream без спроса.
