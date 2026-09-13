"""Russian runtime catalog (MSG_/ERR_/LBL_/HINT_ keys).

Values mirror :mod:`i18n_messages_en`; plural keys carry the three Russian
forms (``one``/``few``/``many``). Style: formal lowercase «вы», imperative
next-actions, «ёлочки» quotes, Arabic numerals. Command names, flag names,
and setting values stay literal English (see the plan's glossary).
"""
from __future__ import annotations

from typing import Any

MESSAGES: dict[str, Any] = {
    # Unified dispatcher help section header.
    "MSG_UNIFIED_OPERATOR_HEADER": (
        "Команды оператора (индексация и обслуживание; подробности — "
        "`jrag <command> --help`):\n"
    ),
    # Absence verdict labels + render prefixes (jrag_render).
    "LBL_ABSENCE_NOT_IN_PROJECT": "нет в проекте",
    "LBL_ABSENCE_EXTERNAL_DEPENDENCY": "внешняя зависимость",
    "LBL_ABSENCE_REFINE_QUERY": "уточните запрос",
    "LBL_ABSENCE_CORRECT_EMPTY": "корректный пустой результат",
    "LBL_VERDICT_PREFIX": "Вердикт: ",
    "LBL_NEXT_PREFIX": "далее: ",
    "LBL_WARNING_PREFIX": "предупреждение: ",
    "LBL_ERROR_PREFIX": "ошибка: ",
    "LBL_ERROR_WORD": "ошибка",
    "LBL_NOT_FOUND_PREFIX": "не найдено: ",
    "LBL_NOT_FOUND_WORD": "не найдено",
    # Truncation notices.
    "MSG_TRUNCATED_OFFSET": "обрезано: результатов больше — используйте --offset {offset}",
    "MSG_TRUNCATED_NARROW": "обрезано: результатов больше — уточните запрос",
    # Did-you-mean (not_found + closest_symbols).
    "MSG_DID_YOU_MEAN_ONE": "Возможно, вы имели в виду: {sym}?",
    "MSG_DID_YOU_MEAN_TWO": "Возможно, вы имели в виду: {a} или {b}?",
    "MSG_DID_YOU_MEAN_MANY": "Возможно, вы имели в виду: {list}?",
    "LBL_OR": " или {last}",
    # Zero-result lines (listing + traversal).
    "MSG_ZERO_LISTING": "{noun}: 0",
    "MSG_EXTERNAL_ENTRYPOINT": "внешняя точка входа — вызывающих в репозитории нет",
    # Row labels.
    "LBL_NO_IDENTIFIER": "(без имени)",
    "LBL_MISSING": "(отсутствует)",
    "LBL_UNRESOLVED": "(не разрешён)",
    "LBL_ROOT_PREFIX": "корень: ",
    # Grouped-traversal headers.
    "LBL_INBOUND": "входящие:",
    "LBL_OUTBOUND": "исходящие:",
    "LBL_SUPERTYPES": "↑ супертипы:",
    "LBL_SUBTYPES": "↓ подтипы:",
    "LBL_STAGE_SEED": "этап 0 (источник):",
    "LBL_STAGE_ROLES": "этап {n} ({roles}):",
    "LBL_STAGE": "этап {n}:",
    # Ambiguous renderer.
    "MSG_AMBIGUOUS_HEADER": {
        "one": "{n} неоднозначное совпадение для '{noun}'",
        "few": "{n} неоднозначных совпадения для '{noun}'",
        "many": "{n} неоднозначных совпадений для '{noun}'",
    },
    "MSG_AMBIGUOUS_HEADER_NO_NOUN": {
        "one": "{n} неоднозначное совпадение",
        "few": "{n} неоднозначных совпадения",
        "many": "{n} неоднозначных совпадений",
    },
    "MSG_NARROW": "Уточните через --kind --java-kind --role --fqn-contains:",
    # Error paths in jrag.main() and authored envelope messages (Task 5).
    "ERR_USAGE_WORD": "ошибка использования",
    "ERR_INTERNAL": "внутренняя ошибка: {exc}",
    "LBL_JRAG_ERROR_STDERR": "jrag: ошибка: ",
    "MSG_INTERRUPTED": "\nПрервано.\n",
    "MSG_NO_INDEX": "Нет индекса в {path}. Выполните: jrag init --source-root <root>",
    "ERR_INDEX_META_FAILED": "не удалось прочитать метаданные индекса: {error}",
    "ERR_INVALID_FILTER": "недопустимый фильтр: {message}",
    "ERR_DESCRIBE_FAILED": "сбой describe",
    "ERR_NEIGHBORS_FAILED": "сбой neighbors_v2",
    "ERR_SEARCH_FAILED": "сбой поиска",
    "ERR_OUTLINE_FAILED": "сбой outline: {exc}",
    "ERR_READ_FAILED": "не удалось прочитать {path}: {exc}",
    "ERR_FILE_NOT_FOUND": (
        "файл не найден: '{file}' (проверены путь как есть и "
        "<source_root>/{file})"
    ),
    "ERR_NO_BACKEND": (
        "языковой backend не зарегистрирован для '{file}' "
        "(суффикс '{suffix}' отсутствует в реестре)"
    ),
    "ERR_INVALID_FRAMEWORK": (
        "недопустимый framework: '{framework}' (нормализован в '{normalized}'); "
        "ожидается одно из: {valid}"
    ),
    "ERR_OVERVIEW_AS_ROUTE": (
        "overview --as route ожидает Route; разрешённый kind: '{kind}'."
    ),
    "MSG_AMBIGUOUS_CANDIDATES": {
        "one": "{n} кандидат",
        "few": "{n} кандидата",
        "many": "{n} кандидатов",
    },
    # Auto-scope notices (stderr line + envelope warnings[] value).
    "MSG_AUTO_SCOPE_STDERR": "[jrag] auto-scope: --service {svc} (по cwd)",
    "WARN_AUTO_SCOPE": (
        "auto-scope: --service {svc} (определён по cwd; "
        "передайте --no-auto-scope, чтобы отключить)"
    ),
    # Watch lifecycle lines.
    "MSG_WATCH_UP": "jrag watch: запущен (pid {pid}, сокет {sock})",
    "MSG_WATCH_DOWN": "jrag watch: не запущен (демона нет в {sock})",
    "MSG_WATCH_STOPPED": "jrag watch: остановлен (pid {pid})",
    "MSG_WATCH_DETACHED": "jrag watch: отсоединён (pid {pid}, сокет {sock}, лог {log})",
    "MSG_WATCH_CHILD_EXITED": "jrag watch: дочерний процесс завершился до готовности (см. {log})",
    "MSG_WATCH_START_TIMEOUT": "jrag watch: не удалось запустить за {seconds}s (см. {log})",
    "MSG_WATCH_LAST_REINDEX": "  последняя переиндексация: {kind} в {when} (всего {count})",
    "MSG_WATCH_LAST_REINDEX_NONE": "  последняя переиндексация: нет (всего {count})",
    "LBL_WATCH_MODE": "  режим: {label}",
    # vocab-index stderr lines.
    "ERR_VOCAB_STDERR": "[ошибка] {exc}",
    "ERR_VOCAB_BUILD_FAILED": "[ошибка] Не удалось построить индекс словаря: {exc}",
    # Operator CLI: lazy advisory twins of the frozen module constants.
    "MSG_INCREMENT_WARNING": (
        "ВНИМАНИЕ: инкрементальная пересборка AST-графа (LadybugDB) пока не реализована.\n"
        "Граф отражает состояние индекса на момент последнего `init` или `reprocess`,\n"
        "поэтому `find`, `neighbors` и `describe` могут возвращать устаревшие результаты\n"
        "для файлов, изменённых с того момента.\n"
        "\n"
        "Векторный индекс Lance обновлён инкрементально и актуален.\n"
        "\n"
        "Чтобы получить актуальный граф, выполните:\n"
        "    jrag reprocess\n"
        "\n"
        "Прогресс инкрементальной пересборки LadybugDB:\n"
        "    {url}"
    ),
    "MSG_REFRESH_DEPRECATION": (
        "ВНИМАНИЕ: команда 'refresh' устарела; используйте 'reprocess'. "
        "Псевдоним будет удалён в следующем релизе."
    ),
    "MSG_REPROCESS_DRIFT_VECTORS_ONLY": (
        "jrag reprocess: пересобраны только векторы; граф (code_graph.lbug) НЕ пересобран "
        "и может отражать устаревший снимок исходников."
    ),
    "MSG_REPROCESS_DRIFT_GRAPH_ONLY": (
        "jrag reprocess: пересобран только граф; векторы (таблицы Lance в "
        "{index_dir}) НЕ пересобраны и могут отражать устаревший снимок исходников."
    ),
    "MSG_VECTORS_SKIPPED_GRAPH_ONLY": (
        "jrag: векторы пропущены — векторный стек не установлен на этой платформе "
        "(режим graph-only). Граф построен/обновлён; семантический поиск недоступен."
    ),
    "MSG_VECTORS_SKIPPED_BM25": (
        "jrag: векторы пропущены — режим retrieval bm25; строится только граф."
    ),
    "MSG_RETRIEVAL_BM25_HINT": (
        "Подсказка: не удаётся скачать модель эмбеддингов? Переключитесь на поиск "
        "по ключевым словам: jrag install --retrieval bm25 (или задайте "
        "JAVA_CODEBASE_RAG_RETRIEVAL=bm25) — индексация и поиск полностью работают офлайн."
    ),
    "MSG_DEPRECATION_NOTICE": (
        "jrag: 'java-codebase-rag' теперь 'jrag'; псевдоним продолжает работать. "
        "Установите JRAG_NO_DEPRECATION=1, чтобы скрыть уведомление.\n"
    ),
    # Operator erase flow.
    "MSG_ERASE_WILL_DELETE": "Будет удалено:",
    "MSG_ERASE_NOTHING": "  (под каталогом индекса нечего удалять)",
    "MSG_ERASE_CONFIRM": "Удалить эти пути? [y/N]: ",
    "MSG_ERASE_NON_INTERACTIVE": (
        "jrag erase: неинтерактивный stdin; передайте --yes для подтверждения."
    ),
    "MSG_ERASE_ABORTED": "Отменено.",
    "MSG_ERASE_COCO_MISSING": (
        "jrag erase: CLI cocoindex не найден рядом с этим Python; "
        "`cocoindex drop` пропущен — cocoindex.db (если был) не удалён CocoIndex."
    ),
    "MSG_ERASE_DROPPED": "jrag: erase: удалены таблицы Lance: {tables}",
    "MSG_WARN_RM_FAILED": "предупреждение: не удалось удалить {path}: {exc}",
    # Reprocess selective-mode TTY lines.
    "MSG_REBUILT_VECTORS": "Пересобрано: векторы",
    "MSG_SKIPPED_GRAPH": (
        "Пропущено: граф (используйте `jrag reprocess --graph-only` или `reprocess` "
        "для обновления)"
    ),
    "MSG_REBUILT_GRAPH": "Пересобрано: граф",
    "MSG_REPROCESS_COMPLETED_VECTORS": "reprocess завершён (только векторы; граф не пересобран)",
    "MSG_REPROCESS_COMPLETED_GRAPH": "reprocess завершён (только граф; векторы не пересобраны)",
    "MSG_REPROCESS_COMPLETED": "reprocess завершён",
    "MSG_REPROCESS_COMPLETED_BM25": "reprocess завершён (graph-only; векторы пропущены — режим retrieval bm25)",
    "MSG_SKIPPED_VECTORS": (
        "Пропущено: векторы (используйте `jrag reprocess --vectors-only` или "
        "`reprocess` для обновления)"
    ),
    # Result-kind nouns.
    "LBL_NOUN_MATCHES": "совпадения",
    "LBL_NOUN_CALLERS": "вызывающие стороны",
    "LBL_NOUN_CALLEES": "вызываемые стороны",
    "LBL_NOUN_IMPLEMENTATIONS": "реализации",
    "LBL_NOUN_SUBCLASSES": "подклассы",
    "LBL_NOUN_OVERRIDES": "переопределения",
    "LBL_NOUN_OVERRIDDEN_BY": "переопределяемые",
    "LBL_NOUN_DEPENDENTS": "зависимые",
    "LBL_NOUN_IMPACT": "затронутые",
    "LBL_NOUN_DECOMPOSE": "этапы",
    "LBL_NOUN_DEPENDENCIES": "зависимости",
    "LBL_NOUN_CONNECTION": "связи",
    "LBL_NOUN_HIERARCHY": "иерархия",
    "LBL_NOUN_ROUTE": "маршруты",
    "LBL_NOUN_CLIENT": "клиенты",
    "LBL_NOUN_PRODUCER": "продюсеры",
    "LBL_NOUN_TOPIC": "топики",
    "LBL_NOUN_SYMBOL": "символы",
    "LBL_NOUN_IMPORT": "импорты",
    "LBL_NOUN_MICROSERVICES": "микросервисы",
    "LBL_NOUN_MAP": "карта",
    "LBL_NOUN_CONVENTIONS": "соглашения",
    "LBL_NOUN_OVERVIEW": "обзор",
    # Installer wizard (Task 7). YAML keys / choice values stay literal.
    "INST_NOTE_HOSTS": "Примечание: можно выбрать несколько агентских хостов через Space. Навигация стрелками.",
    "INST_PROMPT_HOSTS": "Выберите агентские хосты для настройки:",
    "INST_RETRY_HOSTS": "Требуется хотя бы один агентский хост. Выбрать заново?",
    "INST_WILL_DEPLOY": "Будет развёрнуто в: {names}",
    "INST_ERR_UNKNOWN_AGENT": "Ошибка: неизвестный агент '{agent}'. Доступные агенты: {valid}",
    "INST_ERR_AGENT_REQUIRED": "Ошибка: в неинтерактивном режиме требуется флаг --agent.",
    "INST_VALID_AGENTS": "Доступные агенты: {valid}",
    "INST_NOTE_MODULES": "Примечание: выберите модули для индексации. Переключение через Space, подтверждение Enter.",
    "INST_PROMPT_MODULES": "Выберите микросервисы для индексации:",
    "INST_RETRY_MODULES": "Требуется хотя бы один модуль. Выбрать заново?",
    "INST_ERR_SCOPE": "Ошибка: недопустимая область '{scope}'. Должна быть 'project' или 'user'.",
    "INST_NOTE_SCOPE_PROJECT": "Примечание: область 'project' хранит конфигурации в каталоге проекта.",
    "INST_NOTE_SCOPE_USER": "      область 'user' хранит конфигурации в вашем домашнем каталоге.",
    "INST_SELECTED_SCOPE": "Выбранная область: {scope}",
    "INST_ERR_SURFACE": "Ошибка: недопустимая поверхность '{surface}'. Должна быть 'mcp' или 'cli'.",
    "INST_ERR_RETRIEVAL": "Ошибка: недопустимый retrieval '{retrieval}'. Должен быть 'vectors' или 'bm25'.",
    "INST_NOTE_RETRIEVAL": (
        "Примечание: 'vectors' требует модель эмбеддингов (автоматически "
        "загружается с Hugging Face или локальный путь); 'bm25' — поиск по "
        "ключевым словам: без модели, без загрузок, работает офлайн. В режиме "
        "bm25 исходная таблица Lance не ищется (только символы Java/Kotlin)."
    ),
    "INST_PROMPT_RETRIEVAL": "Выберите режим retrieval:",
    "INST_INDEX_EXISTS": "Индекс уже существует. Запустите `jrag reprocess` для пересборки.",
    "INST_NO_CONFIG": "\nКонфигурация проекта не найдена (.java-codebase-rag.yml).",
    "INST_SKIPPING_UPDATE": "Обновление индекса пропущено.",
    "INST_WARN_RESOLVE_FAIL": "\nПредупреждение: не удалось разрешить конфигурацию: {exc}",
    "INST_NO_INDEX": "\nИндекс не найден.",
    "INST_RUN_INSTALL": "Запустите `jrag install`, чтобы создать его.",
    "INST_UPDATE_COMPLETE": "\nОбновление завершено.",
    "INST_UPDATED_ARTIFACTS": "Обновлено артефактов: {n}.",
    "INST_CONFIG_WRITTEN": "Конфигурация записана в {path}",
    "INST_FOUND_CONFIG": "Найдена существующая конфигурация: {path}",
    "INST_CURRENT_CONFIG": "Текущая конфигурация:",
    "INST_WARN_PARSE_FAIL": "Предупреждение: не удалось разобрать существующую конфигурацию: {exc}",
    "INST_WARN_SOME_FAILED_DEPLOY": "Предупреждение: не удалось развернуть некоторые артефакты:",
    "INST_WARN_SOME_FAILED_UPDATE": "\nПредупреждение: не удалось обновить некоторые артефакты:",
    "INST_ARTIFACT_ROW": "  {path}: {error}",
    "INST_CONTINUING": "Продолжаем (конфигурации MCP развёрнуты успешно)...",
    "INST_WOULD_RUN_INCREMENTAL": "\nВыполнилась бы инкрементальная переиндексация (Lance + граф).",
    "INST_ERR_NOT_A_DIR": "Ошибка: путь {path} не существует или не является каталогом.",
    "INST_WARN_MODEL_FALLBACK": "Предупреждение: путь модели {model} не найден, используется 'auto'.",
    # Shared producers: resolve + absence diagnosis (Task 8, spec D7).
    "ERR_INVALID_IDENTIFIER": "Недопустимый идентификатор: {detail}",
    "RS_DETAIL_EMPTY": "пустая строка",
    "RS_DETAIL_WS": "только пробелы",
    "RS_NO_MATCHES": (
        "Совпадений для идентификатора нет; используйте search(query=...) "
        "для нечёткого поиска с ранжированием."
    ),
    "RS_WILDCARDS": (
        "Подстановочные символы (* и ?) в resolve не поддерживаются; "
        "используйте search(query=...) для текстового поиска с ранжированием."
    ),
    "ABS_EXTERNAL": (
        "`{fqn}` используется этим проектом, но не определён в нём ({reason}). "
        "Это внешняя зависимость."
    ),
    "ABS_NO_NODE_ID": (
        "Узла с id `{query}` нет. Запустите `resolve`, чтобы сопоставить "
        "имя/FQN с id, или `search` для поиска символов."
    ),
    "ABS_EMPTY_INDEX": (
        "Индекс выглядит пустым/неиндексированным — убедитесь, что проект "
        "проиндексирован, прежде чем делать вывод об отсутствии символа."
    ),
    "ABS_NL_MISS": (
        "Символ по запросу `{query}` не найден. Уточните запрос — попробуйте "
        "идентификатор (класс/метод/FQN) или просмотрите словарь проекта ниже."
    ),
    "ABS_FILTER_MISS_CLOSE": (
        "Нет результатов для `{identifier}` при текущем фильтре. Есть близкие "
        "совпадения — попробуйте ослабить измерение (см. filter_relaxation)."
    ),
    "ABS_FILTER_MISS": (
        "Нет результатов при текущем фильтре. При других значениях совпадения "
        "есть (см. filter_relaxation)."
    ),
    "ABS_NEIGHBORS_MEANINGFUL": (
        "У `{node}` нет соседей запрошенного типа — это настоящий лист / "
        "внешняя точка входа, а не ошибка."
    ),
    "ABS_NEIGHBORS_MISS": (
        "Нет соседей у `{node}` для запрошенного типа/направления рёбер. "
        "Запустите `describe` и посмотрите `edge_summary`, чтобы узнать, "
        "в каких рёбрах узел реально участвует."
    ),
    "ABS_NOT_IN_PROJECT": (
        "Символа, соответствующего `{query}`, нет в словаре проекта. "
        "Похоже, он здесь не определён."
    ),
    "ABS_CLOSEST": (
        "Точного совпадения с `{query}` нет. Ближайшие символы: {names}. "
        "Уточните запрос (опечатка? область?) и повторите."
    ),
    "ABS_NO_MATCH_PLAIN": "Совпадений по `{query}` нет. Уточните запрос и повторите.",
    "ABS_CAPABILITY_HEAD": "В этом индексе 0 {subject_noun} —",
    "ABS_CAPABILITY_MID": (
        " любой запрос по {subject} вернёт пустой результат независимо от "
        "аргументов — не повторяйте его."
    ),
    "ABS_CAPABILITY_TAIL_REDIRECT": (
        " Если нужно другое, используйте типы рёбер, которые в этом индексе есть (например, {named})."
    ),
    "ABS_CAPABILITY_TAIL_FIND": " Для поиска символов используйте `find`/`search`.",
    "ABS_UNABLE": "Не удалось диагностировать пустой результат; уточните запрос и повторите.",
    # Review follow-up: remaining installer/jrag/cli operator strings.
    "INST_ERR_NO_JAVA": (
        "Ошибка: файлы сборки Java (pom.xml, build.gradle, build.gradle.kts, "
        "build.sbt) не найдены в {root} и его поддереве."
    ),
    "INST_PROMPT_SOURCE_ROOT": "Корень исходников:",
    "INST_MODEL_NOT_FOUND_CONFIRM": "Путь модели {model} не найден. Использовать 'auto'?",
    "INST_PROMPT_MODEL": "Введите путь к модели (или 'auto'):",
    "INST_PROMPT_MODEL_FULL": "Путь к модели эмбеддингов (или 'auto'):",
    "INST_PROMPT_SCOPE": "Выберите область установки:",
    "INST_NOTE_SURFACE_CLI": (
        "Примечание: поверхность 'cli' разворачивает skill+subagent консольной "
        "команды `jrag` (по одной команде на намерение, без MCP-сервера) — рекомендована."
    ),
    "INST_NOTE_SURFACE_MCP": (
        "      поверхность 'mcp' регистрирует MCP-сервер java-codebase-rag "
        "(5 инструментов: search/find/describe/neighbors/resolve)."
    ),
    "INST_PROMPT_SURFACE": "Выберите поверхность агента:",
    "INST_ERR_BINARY_NOT_FOUND": "Ошибка: `{name}` не найден в PATH.",
    "INST_ENSURE_JRAG": (
        "Установите `jrag`, затем повторите с `--non-interactive --agent <host>`."
    ),
    "INST_ENSURE_CONSOLE": (
        "Установите консольную команду `jrag`, "
        "затем повторите с `--non-interactive --agent <host>`."
    ),
    "INST_WARN_BINARY_NOT_FOUND": "Предупреждение: `{name}` не найден в PATH.",
    "INST_PROMPT_BINARY_PATH": "Введите полный путь к {name} (или 'abort'):",
    "INST_ERR_NOT_A_FILE": "Ошибка: путь {path} не существует или не является файлом.",
    "INST_WARN_NOT_EXECUTABLE": "Предупреждение: {path} не исполняемый. Возможны проблемы.",
    "INST_ERR_COCO": "Ошибка: обновление CocoIndex завершилось с кодом {code}",
    "INST_ERR_AST": "Ошибка: сборка AST-графа завершилась с кодом {code}",
    "INST_VECTORS_SKIPPED_INSTALL": (
        "jrag: векторы пропущены — векторный стек не установлен на этой платформе "
        "(режим graph-only). Строится только граф; семантический поиск недоступен."
    ),
    "INST_VECTORS_SKIPPED_UPDATE": (
        "jrag: векторы пропущены — векторный стек не установлен на этой платформе "
        "(режим graph-only). Выполняется только догон графа."
    ),
    "INST_PROMPT_CHOOSE_ACTION": "Выберите действие:",
    "INST_WARN_MARKER_WRITE": "Предупреждение: не удалось записать {path}: {exc}",
    "INST_WOULD_UPDATE_FILE": "Файл {kind} был бы обновлён в {path}",
    "INST_WOULD_CREATE_FILE": "Файл {kind} был бы создан в {path}",
    "INST_UPDATED_FILE": "Файл {kind} обновлён в {path}",
    "INST_WOULD_UPDATE_MCP": "Конфигурация MCP была бы обновлена в {path}",
    "INST_UPDATED_MCP": "Конфигурация MCP обновлена в {path}",
    "INST_WOULD_REMOVE_MCP": "Запись MCP была бы удалена из {path}",
    "INST_REMOVED_MCP": "Запись MCP удалена из {path}",
    "INST_WOULD_REMOVE_FILE": "Было бы удалено: {path}",
    "INST_REMOVED_FILE": "Удалено: {path}",
    "INST_NO_HOSTS": "Настроенные агентские хосты не найдены.",
    "INST_RUN_INSTALL_FIRST": "Сначала выполните `jrag install`.",
    "INST_FOUND_HOSTS": "Найдено настроенных хостов: {n}.",
    "INST_NOTE_MULTI_SURFACE_OWN": (
        "Примечание: настроенные хосты охватывают несколько поверхностей ({surfaces}); "
        "каждая обновляется на своей записанной поверхности (передайте --surface для нормализации)."
    ),
    "INST_NOTE_MULTI_SURFACE_NORMALIZE": (
        "Примечание: настроенные хосты охватывают несколько поверхностей ({surfaces}); "
        "нормализация к '{surface}'."
    ),
    "INST_ERR_MIGRATE_BINARY": (
        "Ошибка: `{name}` не найден в PATH — миграция на поверхность '{surface}' невозможна."
    ),
    "INST_ENSURE_MIGRATE": (
        "Установите `jrag`, затем повторите `update --surface {surface}`."
    ),
    "INST_MIGRATING": "\nМиграция {name} (область {scope}): {from_s} → {to_s}...",
    "INST_WOULD_TEAR_DOWN": (
        "  Артефакты {from_s} были бы снесены, артефакты {to_s} развёрнуты."
    ),
    "INST_REFRESHING": "\nОбновление {name} (область {scope}, surface={surface})...",
    "INST_ERR_LANCE": "Ошибка: обновление индекса Lance завершилось с кодом {code}",
    "INST_WARN_INCREMENTAL_GRAPH": (
        "\nПредупреждение: инкрементальное обновление графа не удалось (код {code}). "
        "Запустите `jrag reprocess` для полной пересборки."
    ),
    "INST_SKIP_MODEL_GRAPH_ONLY": (
        "Выбор модели эмбеддингов пропущен: векторный стек не установлен на этой "
        "платформе (режим graph-only)."
    ),
    "INST_SKIP_MODEL_BM25": (
        "Выбор модели эмбеддингов пропущен: режим retrieval bm25 "
        "(поиск по ключевым словам; модель не нужна)."
    ),
    "INST_ERR_DIR_NOT_WRITABLE": "Каталог недоступен для записи: {path}",
    "INST_ERR_WRITE_FAILED": "Не удалось записать {path}: {exc}",
    "INST_ERR_REMOVE_FAILED": "Не удалось удалить {path}: {exc}",
    # jrag/cli residue.
    "MSG_VOCAB_REBUILT": "Индекс словаря перестроен успешно:",
    "MSG_VOCAB_SYMBOL_COUNT": "  Количество символов: {n}",
    "MSG_VOCAB_SIDECAR": "  Путь сайдкара: {path}",
    "ERR_VOCAB_SAVE_FAILED": "[ошибка] Не удалось сохранить индекс словаря: {exc}",
    "MSG_WATCH_NOT_RUNNING": "jrag watch: не запущен",
    "MSG_PRIME_STDERR": "jrag prime: {msg}",
    "MSG_WARN_EXISTING_CONFIG": (
        "Предупреждение: найдена существующая конфигурация в {path}. "
        "Создание нового проекта здесь создаст отдельный индекс."
    ),
    "MSG_WARN_EXISTING_INDEX": (
        "Предупреждение: найден существующий индекс в {path}. "
        "Создание нового проекта здесь создаст отдельный индекс."
    ),
    "LBL_CLI_ARG_ERROR_STDERR": "jrag: ошибка: ",
    "MSG_INCREMENT_FALLBACK": (
        "[increment] выполнен откат к полной пересборке графа — это нормально "
        "после изменения схемы или первого запуска"
    ),
}
