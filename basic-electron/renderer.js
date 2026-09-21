const textarea = document.querySelector('#editor')

async function load() {
  textarea.value = await window.editor.read()
}

async function save() {
  await window.editor.write(textarea.value)
}

textarea.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 's') {
    event.preventDefault()
    void save()
  }
})

void load()
