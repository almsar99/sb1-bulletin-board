# маркет.код

Дипломный проект — SB1 — Доска объявлений

## Стек

Python 3.14. Версии зависимостей закреплены в `requirements/`.
Тесты и стиль: pytest, Black, isort, Flake8.

## Установка

Нужен Python 3.14.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env
chmod 600 .env
```

Укажите секрет и данные своей базы в `.env`. Переменные оболочки имеют приоритет
над этим файлом.

Секретный ключ:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Настройки

Полный список переменных — в `.env.example`.

| Группа | Зачем |
| --- | --- |
| Приложение | режим, секрет, хосты, адрес сайта, логи |
| Браузер | CSRF и CORS |
| PostgreSQL | имя, пользователь, пароль, хост, порт, SSL |
| JWT | срок access и refresh |
| Почта | сервер, логин, пароль, отправитель |

Значения понадобятся на следующих шагах, когда появится приложение.

## Проверки

```bash
make lint
```
