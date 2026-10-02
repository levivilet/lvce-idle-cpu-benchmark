const {app, BrowserWindow, ipcMain} = require('electron')
const fs = require('node:fs/promises')
const path = require('node:path')

const fixturePath = process.argv.slice(1).find((argument) => {
  const resolved = path.resolve(argument)
  return path.isAbsolute(argument) && resolved !== path.resolve(__dirname) && !argument.startsWith('--')
})

function requireFixture() {
  if (!fixturePath) {
    throw new Error('No fixture file was supplied to the Basic Electron app')
  }
  return fixturePath
}

ipcMain.handle('read-file', async () => fs.readFile(requireFixture(), 'utf8'))
ipcMain.handle('write-file', async (_event, contents) => {
  if (typeof contents !== 'string') {
    throw new TypeError('The editor can only save text')
  }
  await fs.writeFile(requireFixture(), contents, 'utf8')
})

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 720,
    show: false,
    title: 'Basic Electron Editor',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  })
  window.loadFile(path.join(__dirname, 'index.html'))
  window.once('ready-to-show', () => window.show())
}

app.whenReady().then(() => createWindow())
app.on('window-all-closed', () => app.quit())
