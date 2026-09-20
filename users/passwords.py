"""Проверка пароля без перезаписи конкурентного сброса при обновлении хеша."""

from django.contrib.auth.hashers import check_password, make_password, verify_password
from django.db import router


def check_user_password(user, raw_password):
    original_hash = user.password
    is_correct, must_update = verify_password(raw_password, original_hash)
    if not is_correct or not must_update:
        return is_correct

    upgraded_hash = make_password(raw_password)
    if user._state.adding or user._password is not None:
        # Проверка модели или ещё не сохранённого set_password() не должна
        # сама создавать запись либо применять отложенную смену пароля.
        user.password = upgraded_hash
        return True

    model = type(user)
    alias = router.db_for_write(model, instance=user)
    replaced = (
        model._default_manager.using(alias).filter(pk=user.pk, password=original_hash).update(password=upgraded_hash)
    )
    try:
        user.refresh_from_db(using=alias)
    except model.DoesNotExist:
        return False
    user._password = None
    if not user.is_active:
        return False
    if replaced and user.password == upgraded_hash:
        return True
    # Пока вычислялся хеш, другой запрос мог изменить пароль. Проверяем новое
    # значение без setter: устаревший результат проверки больше не даёт вход.
    return check_password(raw_password, user.password)
