/**
 * Главный файл для управления интерфейсом Olympic Robot v4.0
 * Универсальный: standalone-симулятор и веб-интерфейс (с редактором скрипта)
 */

let uploadedScript = null;
let scriptSelected = false;
let mapSelected = false;

/** Возвращает текущий скрипт: из редактора (script-area) или загруженного файла */
function getScriptContent() {
    const ta = document.getElementById('script-area');
    if (ta && ta.value.trim()) return ta.value.trim();
    return uploadedScript ? uploadedScript.content : null;
}

let ui;

document.addEventListener('DOMContentLoaded', () => {
    const hasScriptEditor = !!document.getElementById('script-area');
    ui = window.SimulatorUI.init({
        getScriptContent,
        onMapLoaded: () => { mapSelected = true; },
        updateSimulatorButton
    });

    initFileUpload();
    ui.initMapUpload();
    initDefaultMapLink();
    if (hasScriptEditor) initScriptEditorSync();
    ui.initSimulatorButtons(
        hasScriptEditor
            ? 'Сначала загрузите или введите Python скрипт в блоке выше'
            : 'Сначала загрузите Python скрипт'
    );
});

function initDefaultMapLink() {
    const link = document.getElementById('linkDefaultMap');
    if (!link || !ui.handleMapFile) return;
    link.addEventListener('click', async (e) => {
        e.preventDefault();
        try {
            const resp = await fetch('default_map.json');
            if (!resp.ok) throw new Error(resp.statusText);
            const text = await resp.text();
            const blob = new Blob([text], { type: 'application/json' });
            const file = new File([blob], 'default_map.json', { type: 'application/json' });
            ui.handleMapFile(file);
        } catch (err) {
            ui.showStatus('Ошибка загрузки карты: ' + (err.message || String(err)), 'error');
        }
    });
}

function initFileUpload() {
    const fileInput = document.getElementById('fileInput');
    const uploadSection = document.getElementById('uploadSection');
    if (!fileInput || !uploadSection) return;

    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            handleFile(file);
            e.target.value = '';
        }
    });

    uploadSection.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadSection.classList.add('dragover');
    });

    uploadSection.addEventListener('dragleave', () => {
        uploadSection.classList.remove('dragover');
    });

    uploadSection.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadSection.classList.remove('dragover');
        const file = e.dataTransfer.files[0];
        if (file) {
            handleFile(file);
            fileInput.value = '';
        }
    });
}

function initScriptEditorSync() {
    const ta = document.getElementById('script-area');
    if (!ta) return;
    ta.addEventListener('input', () => {
        scriptSelected = !!getScriptContent();
        updateSimulatorButton();
    });
    ta.addEventListener('change', () => {
        scriptSelected = !!getScriptContent();
        updateSimulatorButton();
    });
    scriptSelected = !!getScriptContent();
    updateSimulatorButton();
}

function handleFile(file) {
    if (!file.name.endsWith('.py')) {
        ui.showStatus('Ошибка: Файл должен быть Python скриптом (.py)', 'error');
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        const content = e.target.result;
        uploadedScript = {
            name: file.name,
            content,
            size: file.size
        };
        const ta = document.getElementById('script-area');
        if (ta) ta.value = content;

        document.getElementById('fileName').textContent = file.name;
        document.getElementById('fileSize').textContent = file.size;
        const fileInfo = document.getElementById('fileInfo');
        if (fileInfo) fileInfo.classList.add('show');

        scriptSelected = true;
        updateSimulatorButton();

        ui.showStatus('Файл успешно загружен!', 'success');

        if (window.simulator && window.simulator.running && (mapSelected || window.customMapData)) {
            window.simulator.stop();
            setTimeout(() => ui.startSimulator({ skipStartButton: true }), 150);
        }
    };
    reader.readAsText(file);
}

function updateSimulatorButton() {
    const btn = document.getElementById('btnSimulator');
    if (!btn) return;
    mapSelected = mapSelected || !!window.customMapData;
    scriptSelected = !!getScriptContent();
    btn.disabled = !(scriptSelected && mapSelected);
}
window.updateSimulatorButton = updateSimulatorButton;
