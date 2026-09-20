# Развёртывание

Нужны Docker и Compose с `up --wait`, доступ к репозиторию и свободный порт.
Без контейнеров — Python 3.14 и PostgreSQL 16, см. README.

В интернет: домен, сертификат, прокси и почта с TLS или SSL.
Compose поднимает только базу и приложение. DNS и сертификаты — отдельно.
На сервере работает Gunicorn, не `runserver`.

Этот файл — общая инструкция. Настройки уже работающего market-kod.ru
(локальный override Compose, каталог фото, nginx) не копируйте из репозитория
поверх боевых файлов.

## Код

```bash
git clone https://github.com/almsar99/sb1-bulletin-board.git
cd sb1-bulletin-board
git switch develop
cp .env.example .env
chmod 600 .env
```

На сервер берите коммит с зелёным CI. `.env` не кладут в Git, архив и образ.

## Локально в Docker

В `.env`: пароль базы и случайный `DJANGO_SECRET_KEY` не короче 50 символов.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Оставьте `DJANGO_SETTINGS_MODULE=config.settings.local`.
В Compose база всегда `db:5432`. Поля хоста и порта нужны, только если Django
запускаете на компьютере, не в контейнере.

```bash
docker compose up --build --wait --wait-timeout 180
docker compose ps
curl --fail http://127.0.0.1:8000/health/
```

Порт сайта — `WEB_PORT`, по умолчанию 8000, только на `127.0.0.1`.
База наружу не открыта. Фото — том `media_data`, пользователь `10001:10001`.
`down` тома оставляет, `down -v` стирает базу, фото и статику.

## Файл для сервера

Скопируйте `.env.example` в `.env.production`, права `600`.
В нём: `ENV_FILE=.env.production`.
`--env-file` — для Compose, `ENV_FILE` — для процесса web. Нужны оба.

| Переменная | Что поставить |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `DJANGO_SECRET_KEY` | свой ключ ≥ 50 символов |
| `DJANGO_ALLOWED_HOSTS` | `market-kod.ru,www.market-kod.ru` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://market-kod.ru` |
| `FRONTEND_URL` | `https://market-kod.ru` |
| `DJANGO_CORS_ALLOWED_ORIGINS` | отдельный фронт или удалить строку |
| `POSTGRES_*` | свои имя, роль, пароль; у db и web одинаковые |
| `DJANGO_CACHE_URL` | в Compose уже `database://message_rate_cache` |
| `JWT_ACCESS_TTL_MINUTES` | 15–60, иначе не стартует |
| `JWT_REFRESH_TTL_DAYS` | 1–7, иначе не стартует |
| `DJANGO_EMAIL_BACKEND` | `smtp` |
| `DJANGO_DEFAULT_FROM_EMAIL` | `noreply@market-kod.ru` |
| `DJANGO_BEHIND_TLS_PROXY` | `true` только за своим прокси |
| `WEB_PORT` | порт на машине, по умолчанию 8000 |

Полный список — `.env.example`. Пустые списки пишите не как `NAME=`, а удаляйте строку.
Почта: SSL и 465 или TLS и 587 — порт и флаги меняйте вместе. `console` на сервере нельзя.

## Запуск на новом сервере

```bash
dc() {
  docker compose --env-file .env.production \
    -f docker-compose.yml -f docker-compose.prod.yml "$@"
}
```

```bash
dc up --build --wait --wait-timeout 180
dc ps
dc exec -T web python manage.py check --deploy --fail-level WARNING
dc exec web python manage.py createsuperuser
```

Контейнер: база → миграции → таблица кэша → статика → Gunicorn.
Упал шаг — сайт не встаёт. Сменили пароль Postgres только в `.env` — том его не подхватит.

На уже работающем market-kod.ru эту функцию `dc` не подставляйте вслепую:
там свой набор Compose-файлов и каталог фото. Сначала раздел «Этот сервер».

## Прокси и фото

Образец: `deploy/nginx.conf`. Это шаблон новой установки.
На живой market-kod.ru его не копируйте: там уже Certbot, свой server и путь к фото.

Статику отдаёт WhiteNoise. Фото — nginx из тома или bind-каталога.
Лимит тела 6 МиБ: файл до 5 МиБ плюс форма.

Приложение само ограничивает вход, регистрацию и сброс.
Прямой доступ к Gunicorn только `127.0.0.1`.
HTTPS рабочий, когда есть живой сертификат, не один заголовок.

## Лимиты

Одни счётчики на сайт, API и короткие адреса.

| Действие | IP | Email |
| --- | --- | --- |
| Вход, включая админку | 20 / мин | 10 / мин |
| Регистрация и повтор письма | 20 / мин | письмо раз в 60 с |
| Сброс пароля | 20 / мин | письмо раз в 60 с |
| Подтверждение сброса | 30 / мин | — |
| Сообщения | 10 / мин | новый диалог раз в 30 с |

Пороги в `AUTH_REQUEST_LIMITS`, не в `.env`.
Несколько процессов без общего кэша считают каждый за себя.

## После запуска

```bash
curl --fail https://market-kod.ru/health/
curl --fail https://market-kod.ru/api/schema/ -o /tmp/schema.yaml
dc exec -T web python manage.py showmigrations
```

Заявка на регистрацию ещё не аккаунт. Ссылка из письма создаёт запись.
Дальше: JWT, объявление, отзыв, чужое править нельзя, фото открывается,
админ входит в `/admin/`.
Повтор ссылки сброса в API — 400. В логах нет паролей и токенов.
На сервере письма не печатаются в консоль.

## Обслуживание

```bash
dc exec -T web python manage.py purge_signup_requests
dc exec -T web python manage.py cleanup_media
```

Первая сносит просроченные заявки. Вторая только показывает лишние фото старше суток.
Удаление — отдельный запуск с `--delete --uploads-stopped` после бэкапа
и остановки записи.

## Копии

Вместе: дамп Postgres и архив фото. Секреты отдельно.

```bash
umask 077
backup_dir="/srv/backups/bulletin-board/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
dc stop web
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump"
dc run --rm -T --no-deps --entrypoint tar web -C /app/media -czf - . > "$backup_dir/media.tar.gz"
dc up -d --wait web
```

Не используйте `down -v` как шаг обновления.

## Обновление

1. Зелёный CI и понятные миграции.
2. Свежие копии.
3. `git pull --ff-only`.
4. `dc up --build --wait`.
5. `check --deploy`.

Откат образа безопасен, только если старая версия понимает уже применённые миграции.

### Этот сервер (market-kod.ru)

Боевой nginx и локальный Compose-override не из этого репозитория.
При выкладке не подкладывайте `deploy/nginx.conf` и не переезжайте на том `media_data`,
пока фото отдаются с диска хоста.

Команды обновления — с тем же именем проекта и тем же набором файлов,
которыми сайт уже запущен. Пути к override и `.env` держите на машине,
не обязательно в git.

Если фото в `./media`, а не в томе — так и оставьте: последний Compose-файл
монтирует каталог в `/app/media`. Новый сервер может сразу использовать том.

## Если сломалось

| Что видите | Куда смотреть |
| --- | --- |
| Нет переменной | `.env.production`, `ENV_FILE`, `--env-file` |
| `Invalid HTTP_HOST` | `DJANGO_ALLOWED_HOSTS`, `HEALTHCHECK_HOST` |
| Цикл HTTPS | прокси, `DJANGO_BEHIND_TLS_PROXY` |
| web/db не встают | `dc logs --tail 100 db web` |
| Фото 404 | том или bind, nginx, права `10001` |
| Письма нет | SMTP; неизвестный адрес письмо не получает |
| 429 | подождать `Retry-After`, общий кэш, IP с прокси |
| 401 | живой access |

Перед публикацией: `DEBUG=False`, точные домены, HTTPS, SMTP,
порт только на loopback, `.env` с правами `600`, `check --deploy` чистый.

### Чеклист публикации

- [ ] HTTPS есть, приложение на `config.settings.prod`, `DEBUG=False`
- [ ] `DJANGO_ALLOWED_HOSTS=market-kod.ru,www.market-kod.ru`
- [ ] `DJANGO_CSRF_TRUSTED_ORIGINS=https://market-kod.ru`
- [ ] `FRONTEND_URL=https://market-kod.ru`
- [ ] Письма с `noreply@market-kod.ru`, SMTP проверен
- [ ] После выкладки: `python manage.py seed_demo` со своими паролями демо-пользователей
- [ ] Ручной путь: пользователь создал карточку на сайте → гость её не видит → админ
открыл `/account/review/` и опубликовал → карточка на главной → автору
пришло письмо → автор поправил текст → снова «На рассмотрении»,
карточка исчезает с главной
- [ ] Кабинет: фильтры «Все», «На рассмотрении», «Опубликовано»,
«Снято с публикации». Очередь админа закрыта для обычного пользователя
