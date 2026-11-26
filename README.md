# Сервис внутренней оценки защиты информации

> Актуальное техническое задание — `docs/sources/REEWORK_TASK.md` от 26.11.2026. Оно заменило прежний одноконтурный учебный сценарий: обследования создаёт администратор, в область входят один или несколько контуров, а журнал значимых действий включён.

Внутренний сервис одной финансовой организации: Django 5.2 / Python 3.12, PostgreSQL 16, Django Templates, Gunicorn, Nginx, Docker Compose. **Нормативная готовность не подтверждена**: полный каталог требует ручной сверки, М-01 по учёту Н остаётся открытым. Отчёт обозначается «Внутренняя оценка / проект отчёта».

Актуальный объём и ограничения: [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md). Проверки: [RELEASE_CHECK.md](docs/RELEASE_CHECK.md). Методика: [METHODOLOGY.md](docs/METHODOLOGY.md).

Порядок работы с интерфейсом: [руководство для администратора, сотрудника и проверяющего](docs/USER_GUIDE.md). Материалы диплома: [исправленные главы 1–2, глава 3 и общий DOCX](docs/thesis/README.md).

Полный состав из 408 нормативных мер загружен отдельной неподтверждённой редакцией. Подготовка, ограничения и порядок сверки: [CATALOG_IMPORT.md](docs/CATALOG_IMPORT.md).

## Работающий стенд

Откройте http://127.0.0.1:8080/accounts/login/ на этой виртуальной машине. Режим разработки использует HTTP и слушает только localhost.

Три демонстрационные роли: demo_admin, demo_employee, demo_reviewer. Случайные пароли лежат в **secrets/demo-accounts.json** (права 0600, исключён из Git). Не публикуйте этот файл. Есть исходный черновик и завершённый синтетический пример с историей исправления. Коды SYN и учебные документы не являются нормативными мерами или данными реального банка.

- Администратор: профиль, контуры, пользователи/роли, назначение участников, импорт и ручная сверка каталога.
- Сотрудник: ответы, версии, приватные файлы, отправка всей анкеты, доработка.
- Проверяющий: создание обследования, запуск заполнения, решения по версиям/исключениям/фактам, расчёт, утверждение, закрытие и экспорт.

Сотрудник и проверяющий одного обследования различаются. Новая версия ответа не наследует решение. После закрытия остаётся чтение; новые оценки оформляются новым обследованием. Административная роль сама не даёт права утверждать ответы.

Проверяющий начинает с кнопки «Заполнить карточку» или «Учебный шаблон SYN». Шаблон подставляет существующий учебный каталог, процессы и даты; новый черновик требует назначения участников администратором. Для просмотра готового примера откройте закрытое обследование: внутри доступны карточка, матрица оценок, применимость, свидетельства, нарушения, расчёт и отчёт. Поиск и фильтры матрицы не меняют область оценки. Памятка с формулами открывается через «Методика».

## Воспроизводимый запуск

Нужны Docker Engine и Compose с поддержкой !override (для TLS-профиля). Из корня:

```bash
python3 scripts/init_dev_env.py
docker compose config --quiet
docker compose build app
docker compose up -d db
docker compose run --rm app python manage.py migrate --noinput
docker compose up -d app nginx
```

init_dev_env.py не перезаписывает существующий .env. PostgreSQL не публикует порт. Свидетельства хранятся в закрытом постоянном томе; у Nginx нет доступа к этому тому. Не выполняйте down -v, если данные нужно сохранить.

Для пустого реального стенда создайте обычного администратора, передав пароль через stdin (без аргумента с паролем):

```bash
docker compose exec app python manage.py bootstrap --username administrator --password-stdin
```

Введите пароль и завершите ввод Ctrl+D. Команда откажется менять существующую учётную запись. Технический superuser для веб-интерфейса не требуется; публичной регистрации нет.

Учебный стенд добавляется только явно и отказывается работать с несинтетическим профилем. Создайте приватный JSON с ключами admin/employee/reviewer, каждый содержит username/password; пример структуры без действующих паролей:

```json
{"admin":{"username":"demo_admin","password":"СЛУЧАЙНЫЙ_ПАРОЛЬ"},"employee":{"username":"demo_employee","password":"ДРУГОЙ_ПАРОЛЬ"},"reviewer":{"username":"demo_reviewer","password":"ТРЕТИЙ_ПАРОЛЬ"}}
```

```bash
docker compose exec -T app python manage.py seed_demo --credentials-stdin < secrets/demo-accounts.json
docker compose exec app python manage.py demo_cycle --assessment ID_ЧЕРНОВИКА
```

demo_cycle создаёт отдельное закрытое учебное обследование, не заменяет исходный черновик и не меняет реальные данные.

## Проверки

У пользователя приложения нет CREATEDB. Один раз создайте отдельную тестовую базу, если её ещё нет:

```bash
docker compose exec db psql -U postgres -d postgres -c 'CREATE DATABASE test_finance OWNER finance'
docker compose exec app python manage.py test --keepdb --noinput
docker compose exec app python manage.py check
docker compose exec app python manage.py makemigrations --check --dry-run
docker compose exec app ruff check .
```

Тесты используют test_finance, не основную finance. После изменения исходников пересоберите app; код не подключён к работающему контейнеру через bind mount. После пересоздания app пересоздайте nginx, чтобы обновить адрес upstream:

```bash
docker compose up -d --build app
docker compose up -d --force-recreate nginx
```

## Локальный HTTPS

```bash
python3 scripts/init_local_tls.py
docker compose -f compose.yaml -f compose.https.yaml up -d app nginx
```

Откройте https://127.0.0.1:8443/. Сертификат самоподписанный, на 30 дней; браузер покажет предупреждение. Ключ остаётся в secrets/tls. UID/GID nginx должны совпасть с владельцем ключа (LOCAL_UID/LOCAL_GID, по умолчанию 1000). Этот профиль включает DEBUG=False, secure cookies, HTTPS redirect и доверие только к закрытому Nginx. Для внешней эксплуатации нужны доверенный сертификат, свои адреса/CSRF origins и отдельная эксплуатационная приёмка; это не автоматический production-деплой.

Возврат к HTTP localhost:

```bash
docker compose up -d app nginx
```

## Данные, резервирование, Git

[Резервное копирование и проверка восстановления](docs/BACKUP_RESTORE.md). Исходники и настройки сохраняются отдельно от томов; .env, secrets/, backups/, private-evidence/ и docs/sources/ исключены из Git. Приложение evidence/ — код, он должен входить в коммит.

Git add добавляет выбранное содержимое в индекс; commit фиксирует индекс локально; push отправляет коммиты в remote. Автоматический push не выполнялся. Перед коммитом проверьте git status и git diff --cached; .gitignore не удаляет секреты из уже созданной истории.

Учебный разбор отложен по запросу автора. Начать его можно с цепочки core/workspace.py → assessments/forms.py → assessments/services.py → модели и шаблон истории.

Журнал значимых действий, история версий ответов, решений, согласований и расчётных снимков сохраняются. Текущее состояние переработки: [PROJECT_STATUS.md](docs/PROJECT_STATUS.md).
