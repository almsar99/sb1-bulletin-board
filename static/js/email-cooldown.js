document.querySelectorAll('form[data-email-form]').forEach((form) => {
  const button = form.querySelector('button[type="submit"]');
  if (!button) return;

  const label = button.textContent.trim();
  const countdown = form.querySelector('[data-email-countdown]');
  const secondsLabel = form.querySelector('[data-email-seconds]');
  const retryAfter = Number(form.dataset.emailRetryAfter) || 0;
  // A deadline keeps the timer accurate when a background tab pauses callbacks.
  const deadline = Date.now() + Math.max(0, retryAfter) * 1000;
  let submitting = false;
  let timer;

  const secondsRemaining = () => Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  const update = () => {
    const seconds = secondsRemaining();
    button.disabled = submitting || seconds > 0;
    button.textContent = submitting ? 'Отправляем…' : label;
    if (secondsLabel) secondsLabel.textContent = String(seconds);
    if (countdown) countdown.hidden = seconds === 0;
    if (seconds === 0 && timer) {
      clearInterval(timer);
      timer = undefined;
    }
  };

  form.addEventListener('submit', (event) => {
    if (event.defaultPrevented) return;
    if (submitting || secondsRemaining() > 0) {
      event.preventDefault();
      return;
    }
    // Native validation completes before the browser dispatches submit.
    submitting = true;
    form.setAttribute('aria-busy', 'true');
    update();
  });

  window.addEventListener('pageshow', () => {
    // Returning with Back restores the form without restarting its cooldown.
    submitting = false;
    form.removeAttribute('aria-busy');
    update();
  });

  update();
  if (secondsRemaining() > 0) timer = setInterval(update, 250);
});
