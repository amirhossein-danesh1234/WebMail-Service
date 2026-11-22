const emailProbe = document.createElement('input');
emailProbe.type = 'email';

function completeAddress(value) {
  emailProbe.value = value;
  return /^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(value) && emailProbe.validity.valid;
}

document.querySelectorAll('input[data-recipient]').forEach((input) => {
  const selected = new Map();
  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.name = input.name;
  hidden.value = input.value;
  input.removeAttribute('name');

  const container = document.createElement('div');
  container.className = 'recipient-entry';
  const chips = document.createElement('div');
  chips.className = 'recipient-chips';
  input.before(container);
  container.append(chips, input, hidden);
  input.setAttribute('inputmode', 'email');
  input.spellcheck = false;

  const sync = () => {
    hidden.value = [...selected.values(), input.value.trim()].filter(Boolean).join(', ');
  };
  const add = (address) => {
    const key = address.toLowerCase();
    if (selected.has(key)) return;
    selected.set(key, address);
    const chip = document.createElement('span');
    chip.className = 'recipient-chip';
    chip.textContent = address;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', 'Remove ' + address);
    remove.addEventListener('click', () => {
      selected.delete(key);
      chip.remove();
      sync();
      input.focus();
    });
    chip.append(remove);
    chips.append(chip);
  };
  const consumeList = () => {
    const tokens = input.value.trim().split(/[ ,;،\t]+/).filter(Boolean);
    if (!tokens.length || !tokens.every(completeAddress) || selected.size + tokens.length > 20) return false;
    input.value = '';
    tokens.forEach(add);
    sync();
    return true;
  };

  consumeList();
  input.addEventListener('keydown', (event) => {
    if (event.isComposing || ![' ', 'Enter', ',', ';', '،'].includes(event.key)) return;
    if (completeAddress(input.value.trim()) && selected.size < 20) {
      event.preventDefault();
      consumeList();
    } else if (event.key === 'Enter') {
      event.preventDefault();
    }
  });
  input.addEventListener('input', () => {
    if (/[ ,;،\t]$/.test(input.value)) consumeList();
    sync();
  });
  input.addEventListener('blur', () => consumeList());
  input.form.addEventListener('submit', () => {
    consumeList();
    sync();
  });
});

const panel = document.querySelector('[data-compose]');
if (panel) {
  const expand = panel.querySelector('[data-expand]');
  const setExpanded = (expanded) => {
    panel.classList.toggle('expanded', expanded);
    expand.setAttribute('aria-pressed', String(expanded));
    expand.textContent = expanded ? 'Restore' : 'Expand';
  };
  expand.hidden = false;
  expand.addEventListener('click', () => setExpanded(expand.getAttribute('aria-pressed') !== 'true'));
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && expand.getAttribute('aria-pressed') === 'true') {
      setExpanded(false);
      expand.focus();
    }
  });
}

const editor = panel?.querySelector('[data-rich-editor]');
if (editor && typeof document.execCommand === 'function' && document.queryCommandSupported('bold')) {
  const toolbar = panel.querySelector('[data-format-toolbar]');
  const text = panel.querySelector('textarea[name="body"]');
  const markup = panel.querySelector('input[name="body_html"]');
  const allowed = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE', 'DEL', 'P', 'DIV', 'BR', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'A']);

  const safeLink = (href) => {
    if (!href) return false;
    try {
      return ['https:', 'http:', 'mailto:'].includes(new URL(href).protocol);
    } catch {
      return false;
    }
  };

  function appendSafe(parent, node) {
    if (node.nodeType === Node.TEXT_NODE) {
      parent.append(node.textContent);
    } else if (allowed.has(node.nodeName)) {
      const child = document.createElement(node.nodeName.toLowerCase());
      if (node.nodeName === 'A' && safeLink(node.getAttribute('href'))) child.href = node.getAttribute('href');
      node.childNodes.forEach((item) => appendSafe(child, item));
      parent.append(child);
    }
  }

  const restoredMarkup = editor.dataset.restoredMarkup;
  let editedHtml = Boolean(restoredMarkup);
  if (editedHtml) {
    const inert = new DOMParser().parseFromString(restoredMarkup, 'text/html');
    inert.body.childNodes.forEach((child) => appendSafe(editor, child));
  } else {
    editor.textContent = text.value;
  }

  const syncEditor = () => {
    text.value = editor.innerText;
    markup.value = editedHtml ? editor.innerHTML : '';
  };
  editor.contentEditable = 'true';
  editor.hidden = false;
  toolbar.hidden = false;
  text.hidden = true;
  panel.querySelector('label[for="id_body"]').htmlFor = 'compose-editor';
  editor.id = 'compose-editor';
  editor.addEventListener('input', syncEditor);
  editor.addEventListener('paste', (event) => {
    event.preventDefault();
    document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
    syncEditor();
  });
  editor.addEventListener('drop', (event) => event.preventDefault());
  toolbar.querySelectorAll('button[data-format]').forEach((button) => {
    const command = button.dataset.format;
    if (!document.queryCommandSupported(command)) {
      button.hidden = true;
      return;
    }
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', () => {
      const selection = window.getSelection();
      const saved = selection.rangeCount && editor.contains(selection.anchorNode) ? selection.getRangeAt(0).cloneRange() : null;
      const argument = command === 'createLink' ? window.prompt('Link URL', 'https://') : null;
      if (command === 'createLink' && !safeLink(argument)) return;
      editor.focus();
      if (saved) {
        selection.removeAllRanges();
        selection.addRange(saved);
      }
      if (document.execCommand(command, false, argument)) editedHtml = true;
      syncEditor();
    });
  });
  text.form.addEventListener('submit', syncEditor);
}
