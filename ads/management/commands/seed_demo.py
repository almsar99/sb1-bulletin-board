"""Повторяемое наполнение с журналом происхождения в штатном Django LogEntry."""

from django.conf import settings
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from ads.models import Ad, AdCategory, AdStatus, Review
from messaging.models import Message, Thread
from users.models import User

MARKER = "seed_demo:v1:"
ACCOUNTS = (
    ("admin", "demo-admin@market-kod.ru", "Администратор"),
    ("user1", "demo-anna@market-kod.ru", "Анна"),
    ("user2", "demo-max@market-kod.ru", "Максим"),
)
TITLES = (
    (
        "Парсер открытых данных на Python",
        "Автоматическая выгрузка открытых данных в CSV. Инструкция по настройке и примеры запросов.",
    ),
    ("Разработка API на Django", "Спроектирую и реализую API для вашего проекта. Документация и тесты включены."),
    ("Практикум по SQL", "Четыре занятия с практикой: запросы, индексы и оптимизация. Материалы остаются у вас."),
    ("Шаблон панели аналитики", "Готовая панель с графиками, экспортом и настройкой источников данных."),
    (
        "Клавиатура для рабочего места",
        "Механическая клавиатура с тихими переключателями. Полный комплект, аккуратное использование.",
    ),
    (
        "Скрипт резервного копирования",
        "Резервные копии с проверкой целостности и уведомлениями. Поддерживает локальное хранилище.",
    ),
    ("Адаптивная вёрстка сайта", "Создам страницы по макету с поддержкой мобильных устройств и доступной навигацией."),
    (
        "Основы автоматизации на Python",
        "Практический курс для начинающих: файлы, таблицы и автоматизация ежедневных задач.",
    ),
    (
        "Система записи на консультации",
        "Календарь, запись клиентов и почтовые уведомления. Подходит для небольшого бизнеса.",
    ),
    ("Монитор 27 дюймов", "IPS-монитор для работы с кодом и документами. Подставка и кабели в комплекте."),
    ("Инструмент проверки ссылок", "Проверка страниц и отчёт о неработающих ссылках. Запуск из командной строки."),
    (
        "Настройка тестирования проекта",
        "Помогу внедрить тесты и проверку качества кода. Обсудим задачи на первой встрече.",
    ),
    ("Введение в Linux", "Уверенная работа с терминалом, файлами и процессами. Упражнения с обратной связью."),
    (
        "Каталог для небольшого магазина",
        "Готовые страницы каталога, поиска и управления товарами. Установка по инструкции.",
    ),
    (
        "Мини-компьютер для лаборатории",
        "Компактный компьютер для экспериментов и учебных проектов. Проверен, готов к работе.",
    ),
    (
        "Утилита обработки изображений",
        "Пакетное изменение размеров и форматов. Настраиваемые профили для разных задач.",
    ),
    ("Аудит производительности сайта", "Найду узкие места и подготовлю перечень улучшений с приоритетами."),
    ("Практика работы с Git", "Ветки, слияния и командная работа на небольшом учебном проекте."),
    ("База знаний для команды", "Поиск по заметкам и документам, разделы и управление доступом."),
    (
        "Комплект для изучения электроники",
        "Макетная плата, провода и датчики для первых проектов. Список компонентов прилагается.",
    ),
)


class Command(BaseCommand):
    help = "Создать демонстрационные данные маркет.код; --flush удаляет только отмеченные записи."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Только очистить созданные командой записи")
        for key, _, _ in ACCOUNTS:
            parser.add_argument(f"--{key}-password", help="Пароль новой демонстрационной учётной записи")

    @transaction.atomic
    def handle(self, *args, **options):
        # Обеспечиваем последовательное выполнение команды в разных процессах без новой модели или миграции.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [902609])
        self.entries = {
            entry.change_message: entry for entry in LogEntry.objects.filter(change_message__startswith=MARKER)
        }
        if options["flush"]:
            self.flush()
            return
        passwords = {}
        for key, _, _ in ACCOUNTS:
            passwords[key] = options[f"{key}_password"] or (f"Demo-{key}-2026!" if settings.DEBUG else None)
            if not passwords[key]:
                raise CommandError("Вне DEBUG задайте --admin-password, --user1-password и --user2-password.")
        users = []
        for key, email, name in ACCOUNTS:
            account = self.existing(key, User)
            if account is None:
                if User.objects.filter(email=email).exists():
                    raise CommandError(f"Адрес {email} уже занят записью, не созданной seed_demo.")
                account = User.objects.create_user(
                    email=email,
                    password=passwords[key],
                    email_verified_at=timezone.now(),
                    first_name=name,
                    role="admin" if key == "admin" else "user",
                    is_staff=key == "admin",
                    is_superuser=key == "admin",
                )
                self.record(key, account, users[0] if users else account)
            users.append(account)
        for index, (title, description) in enumerate(TITLES):
            author = users[1 + index % 2]
            ad = self.existing(f"ad-{index}", Ad)
            if ad is None:
                ad = Ad.objects.create(
                    title=title,
                    description=description,
                    price=[1900, 15000, 4900, 7900, 6500][index % 5] + index * 100,
                    author=author,
                    category=AdCategory.values[index % 5],
                    status=(
                        AdStatus.DRAFT
                        if index in (7, 14, 19)
                        else AdStatus.ARCHIVED if index in (12, 18) else AdStatus.PUBLISHED
                    ),
                    submitted_at=timezone.now() if index in (7, 14, 19) else None,
                )
                self.record(f"ad-{index}", ad, users[0])
            if index < 10:
                guest = users[2 if author == users[1] else 1]
                for number, text in enumerate(
                    ("Описание понятное, спасибо за подробности.", "Полезное предложение, всё соответствует описанию.")
                ):
                    self.message(f"review-{index}-{number}", ad, guest, users[0], text=text)
                question = self.message(
                    f"question-{index}",
                    ad,
                    guest,
                    users[0],
                    text="Можно уточнить условия и комплектацию?",
                    kind="question",
                )
                if index < 5:
                    self.message(
                        f"answer-{index}",
                        ad,
                        author,
                        users[0],
                        text="Да, все условия указаны в описании. Отвечу на дополнительные вопросы.",
                        kind="question",
                        parent=question,
                    )
        self.seed_threads(users)
        self.stdout.write(self.style.SUCCESS("Демонстрационные записи созданы. Повторный запуск не дублирует данные."))

    def seed_threads(self, users):
        for index in range(3):
            ad = self.existing(f"ad-{index}", Ad)
            guest = users[2 if ad.author_id == users[1].pk else 1]
            thread = self.existing(f"thread-{index}", Thread)
            if thread is None:
                thread, created = Thread.objects.get_or_create(ad=ad, initiator=guest)
                if not created:
                    # Не добавляем демонстрационные сообщения и отметки происхождения в настоящий диалог.
                    continue
                self.record(f"thread-{index}", thread, users[0])
            for number, text in enumerate(
                (
                    "Здравствуйте! Предложение ещё актуально?",
                    "Да, актуально. Что вас интересует?",
                    "Расскажите, пожалуйста, об условиях.",
                )
            ):
                key = f"private-{index}-{number}"
                if self.existing(key, Message) is None:
                    item = Message.objects.create(
                        thread=thread,
                        author=ad.author if number == 1 else guest,
                        text=text,
                        read_at=timezone.now() if number == 0 else None,
                    )
                    Thread.objects.filter(pk=thread.pk).update(updated_at=item.created_at)
                    self.record(key, item, users[0])

    def existing(self, key, model):
        entry = self.entries.get(MARKER + key)
        if entry and entry.content_type_id == ContentType.objects.get_for_model(model).pk:
            return model.objects.filter(pk=entry.object_id).first()
        return None

    def record(self, key, obj, actor):
        old = self.entries.get(MARKER + key)
        if old:
            old.delete()
        entry = LogEntry.objects.create(
            user=actor,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=str(obj.pk),
            object_repr=str(obj)[:200],
            action_flag=ADDITION,
            change_message=MARKER + key,
        )
        self.entries[MARKER + key] = entry

    def message(self, key, ad, author, actor, **values):
        obj = self.existing(key, Review)
        if obj is None:
            obj = Review.objects.create(ad=ad, author=author, **values)
            self.record(key, obj, actor)
        return obj

    def flush(self):
        removed = kept = 0
        # Удаляем ответы до вопросов, объявления до пользователей, владельца журнала — последним.
        ordered = sorted(
            self.entries.items(),
            key=lambda item: (
                -2
                if ":private-" in item[0]
                else (
                    -1
                    if ":thread-" in item[0]
                    else (
                        0
                        if ":answer-" in item[0]
                        else (
                            1
                            if ":review-" in item[0]
                            else (
                                2
                                if ":question-" in item[0]
                                else 3 if ":ad-" in item[0] else 5 if item[0] == MARKER + "admin" else 4
                            )
                        )
                    )
                )
            ),
        )
        for _, entry in ordered:
            try:
                obj = entry.get_edited_object()
            except ObjectDoesNotExist:
                obj = None
            if obj is None:
                entry.delete()
                continue
            dependent = (
                isinstance(obj, Review)
                and obj.answers.exists()
                or isinstance(obj, Ad)
                and (obj.reviews.exists() or obj.threads.exists())
                or isinstance(obj, User)
                and (
                    obj.ads.exists()
                    or obj.reviews.exists()
                    or obj.initiated_threads.exists()
                    or obj.private_messages.exists()
                )
                or isinstance(obj, Thread)
                and obj.messages.exists()
            )
            if dependent:
                kept += 1
                continue
            # Если записи сохранены, сохраняем и пользователя, которому принадлежат отметки их происхождения.
            if (
                isinstance(obj, User)
                and LogEntry.objects.filter(user=obj, change_message__startswith=MARKER).exclude(pk=entry.pk).exists()
            ):
                kept += 1
                continue
            obj.delete()
            entry.delete()
            removed += 1
        self.stdout.write(f"Удалено записей: {removed}. Сохранено из-за связанных записей: {kept}.")
