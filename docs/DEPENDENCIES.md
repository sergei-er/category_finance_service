# Зависимости приложения

Проверено 09.11.2026. Ветки Python 3.12 / Django 5.2 / PostgreSQL 16 сохранены. Точные версии:

| Компонент | Версия | Применение / источник |
|---|---|---|
| Python | 3.12.16 | `.python-version`, Dockerfile; [официальный образ](https://hub.docker.com/_/python) |
| Django | 5.2.19 | [релиз безопасности](https://www.djangoproject.com/weblog/2026/nov/03/security-releases/) |
| Psycopg | 3.3.8, binary той же версии | [PyPI проекта](https://pypi.org/project/psycopg/); PostgreSQL-драйвер |
| Gunicorn | 26.2.0 | [PyPI проекта](https://pypi.org/project/gunicorn/) |
| Ruff | 0.17.4 | [PyPI проекта](https://pypi.org/project/ruff/); только разработка |
| PostgreSQL | образ 16.15-bookworm | [ветки поддержки](https://www.postgresql.org/support/versioning/) и [образ](https://hub.docker.com/_/postgres) |
| Nginx | образ 1.30.6-alpine | [официальный образ](https://hub.docker.com/_/nginx) |

Прямые зависимости находятся в requirements.in / requirements-dev.in. requirements.txt и requirements-dev.txt закрепляют также транзитивные версии и SHA-256 дистрибутивов; скомпилированы uv 0.13.7 для Python 3.12. Установка в Docker выполняется `pip install --require-hashes`. uv использовался как временный инструмент агента, не является зависимостью приложения. PyYAML 6.0.3 использовался временно для чтения Compose при проверке, не включён в приложение.

Обновление: проверить поддерживаемые версии первичных проектов, изменить .in, пересобрать оба .txt с `uv pip compile --python-version 3.12 --generate-hashes`, просмотреть diff и повторить проверки. Не менять только .in без файлов .txt. Выход за закреплённые архитектурные ветки не разрешён.

Теги образов закреплены по patch-версиям, но не по digest: содержимое образов может обновляться. Полная побитовая воспроизводимость образов пока не заявляется. Образы собраны и приложение проверено в Docker на PostgreSQL 16; результаты см. RELEASE_CHECK.md.

Экспорт DOCX: python-docx 1.2.0; транзитивно lxml 6.1.3 и typing-extensions 4.16.0. Все закреплены вместе с хэшами в requirements*.txt.
