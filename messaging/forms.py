from django import forms


class MessageForm(forms.Form):
    text = forms.CharField(
        label="Сообщение",
        max_length=2000,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Напишите сообщение"}),
        error_messages={"required": "Введите сообщение.", "max_length": "Не более 2000 символов."},
    )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("auto_id", "private_%s")
        super().__init__(*args, **kwargs)
