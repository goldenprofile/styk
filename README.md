# Styk

Прототип локального Orchestrator–Workers runtime: явный JSON-план, последовательные задачи, файловые артефакты и проверки вне LLM. Можно использовать из терминала или подключить к Pi Coding Agent.

## Требования

- Linux / POSIX, Python 3.12+; runtime использует только стандартную библиотеку.
- Для реальных LLM-задач: установленный и авторизованный Pi с RPC, событием `agent_settled` и флагами отключения extensions/MCP.
- Node.js требуется самому Pi, но не Python-ядру.

Команды ниже выполняются из корня этого репозитория. Для запуска из исходников установка не требуется.

Установка CLI в виртуальное окружение (нужны модули `venv` и `pip`):

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
styk --help
```

Для сборки wheel: `python -m pip wheel --no-deps . --wheel-dir dist`.
Runtime не имеет внешних зависимостей; для упаковки используется setuptools.
Имя `styk` в метаданных локального пакета не означает публикацию в PyPI.

## Быстрый запуск без модели

```bash
demo_dir=$(mktemp -d /tmp/styk-demo-XXXXXX)
PYTHONPATH=src python3 -m styk --workspace "$demo_dir" run examples/demo.json
PYTHONPATH=src python3 -m styk --workspace "$demo_dir" status
```

Demo выполняет scout, создаёт `hello.txt` и проверяет точное содержимое отдельным процессом. Существующий файл не перезаписывается. Это детерминированный backend для проверки ядра, не симуляция качества модели.

## Запуск с Pi

Сначала проверьте Pi отдельно: `pi --version`, `pi --help` и авторизацию выбранной модели. Runtime не устанавливает Pi и не запрашивает ключи.

```bash
work_dir=$(mktemp -d /tmp/styk-pi-XXXXXX)
PYTHONPATH=src python3 -m styk --workspace "$work_dir" run examples/pi.json
```

В копии плана можно задать `model`, `provider`, `pi_executable`. Для своей задачи измените `objective`, `acceptance_criteria` и `checks`. Проверки задаются массивами аргументов без неявного shell. Они запускают доверенный вами код с правами текущего пользователя.

Pi запускается с отдельной непостоянной сессией для каждой попытки; discovered extensions, MCP, skills и templates отключены. Scout получает чтение и поиск, builder — также edit/write/bash. Завершение определяется по `agent_settled`, а результат — JSON из последнего ответа assistant. Некорректный JSON не принимается.

Контракт результата пока минимальный: `{"outcome":"COMPLETED","summary":"..."}` либо `BLOCKED`. Проверки, идентификаторы и ссылки на артефакты добавляет runtime. Структурированные критерии из плана остаются частью задания: человек должен убедиться, что команды действительно проверяют их.

## Использование внутри Pi

Из нужного рабочего проекта запустите Pi с расширением этого репозитория:

```bash
pi -e /absolute/path/styk/integrations/pi/styk.ts
```

В Pi:

```text
/styk-run /absolute/path/styk/examples/pi.json
/styk-cancel
```

Расширение показывает последнюю строку статуса. Команда запуска немедленно возвращает управление интерфейсу. По завершении выводится краткий итог. `STYK_PYTHON` позволяет выбрать Python executable. Не запускайте одновременно обычные изменения главного Pi и Styk builder в одном рабочем дереве.

Расширение написано под типы `@earendil-works/pi-coding-agent`; на старых версиях может потребоваться адаптация. В текущем окружении нет Node.js/Pi: расширение и вызовы реальной модели ещё не проверены. RPC-адаптер покрыт тестом с отдельным эмулятором процесса.

## Состояние и продолжение

```bash
PYTHONPATH=src python3 -m styk --workspace /path/to/project status
PYTHONPATH=src python3 -m styk --workspace /path/to/project status r-012345abcdef
PYTHONPATH=src python3 -m styk --workspace /path/to/project resume r-012345abcdef
```

Ctrl+C отменяет запуск, завершает управляемые процессы и сохраняет состояние. CLI возвращает 0 при успехе, 1 при неуспехе/блокировке, 2 при ошибке ввода, 130 при отмене.

Resume пропускает принятые задачи, сохраняет общий счётчик попыток и отказывается продолжать, если содержимое проекта отличается от последней контрольной точки. При исчерпанном лимите создайте новый план/запуск. После аварийного SIGKILL возможен отказ возобновления: незавершённые изменения сначала нужно проверить вручную.

```text
.agent-runs/<run_id>/
├── plan.json
├── state.json
├── events.jsonl
└── tasks/<task_id>/attempts/a-01/
    ├── task.json
    ├── result.json
    ├── report.md
    ├── check-0.log
    ├── pi.events.jsonl     # Pi backend
    └── pi.stderr.log       # Pi backend
```

Результаты каждой попытки сохраняются отдельно; state JSON публикуется атомарно. Блокировка `.agent-runs/.lock` запрещает одновременные запуски Styk, но не блокирует редактор или другой агент.

## Проверки разработки

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

Тесты проверяют полный demo, повторы с диагностикой, лимиты, блокировку, отмену, изменение файлов перед resume, отсутствие Pi и RPC lifecycle. Внешние зависимости не нужны.

Ruff настроен в `pyproject.toml`. Если он установлен отдельно:

```bash
ruff check src tests
ruff format --check src tests
```

## Границы прототипа

- Один последовательный список scout/builder, зависимости только на предыдущие задачи; LLM Supervisor пока заменён явным планом пользователя.
- Нет автоматического tier routing, стоимостного бюджета, общего deadline запуска, worktree, merge, Codex/Claude адаптеров.
- Тайм-аут применяется к вызову worker и к каждой проверке отдельно; число попыток ограничено.
- Нет OS sandbox: инструменты Pi и тесты имеют права пользователя. Read-only обеспечивается выбором инструментов и обнаружением изменений после исполнения, не изоляцией ОС.
- Нет отката, сохранения полного patch, мониторинга внешних изменений во время записи и гарантированного удаления секретов из логов. Логи могут быть чувствительными.
- Снимок исключает `.git`, `.agent-runs`, `.venv`, `node_modules`, Python/Ruff caches. Это fingerprint файлов, не резервная копия и не полная транзакция.
- Реальная интеграция Pi и TypeScript extension требуют проверки в среде с Pi; реализован протокол из текущей официальной документации, не совместимость со всеми версиями.

## Структура и документация

`src/styk/` — CLI, runtime, хранилище, процессы и backends; `tests/` — интеграционные тесты; `examples/` — планы; `integrations/pi/` — тонкое расширение; [docs/](docs/README.md) — архитектура.

Источники протокола: [Pi RPC](https://pi.dev/docs/latest/rpc), [CLI](https://pi.dev/docs/latest/cli), [JSON events](https://pi.dev/docs/latest/json), [Extensions](https://pi.dev/docs/latest/extensions).

## Первый push

Создайте пустой репозиторий GitHub без README, лицензии и `.gitignore`, затем из корня проекта:

```bash
git branch -M main
git add .gitignore AGENTS.md README.md pyproject.toml .github docs examples integrations src tests
git diff --cached --check
git diff --cached --stat
git commit -m "Add Styk runtime prototype and Pi integration"
git remote add origin git@github.com:YOUR_ACCOUNT/styk.git
git push -u origin main
```

Замените `YOUR_ACCOUNT` своим именем GitHub. Лицензия пока не выбрана и не добавлена.
