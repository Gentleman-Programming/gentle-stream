(() => {
  const commands = document.getElementById('install-commands');
  const button = document.getElementById('copy-commands');
  const status = document.getElementById('copy-status');
  let copying = false;

  button.addEventListener('click', async () => {
    if (copying) return;
    copying = true;
    status.textContent = '';

    try {
      await navigator.clipboard.writeText(commands.textContent);
      status.textContent = 'Commands copied.';
    } catch {
      status.textContent = 'Could not copy. Select and copy the commands manually.';
    } finally {
      copying = false;
    }
  });

  button.hidden = false;
})();
