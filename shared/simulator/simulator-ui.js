/**
 * Общая логика UI симулятора Olympic Robot v4.0
 * Вызывающая сторона передаёт getScriptContent() и callbacks для обновления состояния
 */
(function() {
    'use strict';

    let getScriptContent = () => null;
    let onMapLoaded = () => {};
    let updateSimulatorButton = () => {};

    function appendSimLog(level, message) {
        const box = document.getElementById('debug-log');
        if (!box) return;
        const ts = new Date().toLocaleTimeString();
        const line = `[${ts}] [${level}] ${message}\n`;
        box.textContent += line;
        box.scrollTop = box.scrollHeight;
    }
    window.appendSimulatorLog = appendSimLog;

    function showStatus(message, type) {
        const statusEl = document.getElementById('status');
        if (!statusEl) return;
        statusEl.textContent = message;
        statusEl.className = `status ${type} show`;
        if (type === 'success' || type === 'info') {
            setTimeout(() => statusEl.classList.remove('show'), 5000);
        }
    }

    function handleMapFile(file) {
        if (!file || !file.name) return;
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const payload = JSON.parse(e.target.result);
                const ver = payload.version;
                if (ver !== 1) {
                    showStatus('Неизвестная версия файла карты: ' + ver, 'error');
                    return;
                }
                const w = Number(payload.mapWidth) || 800;
                const h = Number(payload.mapHeight) || 600;
                if (w !== 800 || h !== 600) {
                    showStatus('Размер карты в файле (' + w + '×' + h + ') не совпадает с симулятором (800×600).', 'error');
                    return;
                }
                const base64 = payload.imageDataBase64;
                if (!base64 || typeof base64 !== 'string') {
                    showStatus('В файле нет данных изображения карты.', 'error');
                    return;
                }
                const raw = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
                const tmpCanvas = document.createElement('canvas');
                tmpCanvas.width = w;
                tmpCanvas.height = h;
                const tmpCtx = tmpCanvas.getContext('2d');
                const imageData = tmpCtx.createImageData(w, h);
                const len = Math.min(raw.length, imageData.data.length);
                for (let i = 0; i < len; i++) imageData.data[i] = raw[i];

                const ZONE_RADIUS = 25;
                const LAMP_INFLUENCE_RADIUS = 120;

                const cameraZones = (payload.cameraZones && Array.isArray(payload.cameraZones))
                    ? payload.cameraZones.map(z => ({
                        x: Number(z.x) || 0,
                        y: Number(z.y) || 0,
                        radius: Number(z.radius) > 0 ? Number(z.radius) : ZONE_RADIUS,
                        sign: z.sign
                    }))
                    : [];

                const lamps = (payload.lamps && Array.isArray(payload.lamps))
                    ? payload.lamps.map(l => ({
                        x: Number(l.x) || 0,
                        y: Number(l.y) || 0,
                        radius: Number(l.radius) > 0 ? Number(l.radius) : LAMP_INFLUENCE_RADIUS
                    }))
                    : [];

                const ultrasonicObstacles = (payload.ultrasonicObstacles && Array.isArray(payload.ultrasonicObstacles))
                    ? payload.ultrasonicObstacles.map(obs => ({
                        x: Number(obs.x) || 0,
                        y: Number(obs.y) || 0,
                        radius: Number(obs.radius) > 0 ? Number(obs.radius) : 30
                    }))
                    : [];

                const sp = payload.startPosition;
                const startPosition = (sp && typeof sp.x === 'number' && typeof sp.y === 'number')
                    ? { x: sp.x, y: sp.y, angle: Number(sp.angle) || 90 }
                    : null;

                window.customMapData = {
                    imageData,
                    cameraZones,
                    lamps,
                    ultrasonicObstacles,
                    startPosition
                };

                const mapNameEl = document.getElementById('mapFileName');
                const mapInfoEl = document.getElementById('mapFileInfo');
                if (mapNameEl && mapInfoEl) {
                    mapNameEl.textContent = file.name;
                    mapInfoEl.classList.add('show');
                }

                onMapLoaded();
                updateSimulatorButton();
                showStatus('Карта успешно загружена.', 'success');

                if (window.simulator && window.simulator.running && getScriptContent()) {
                    window.simulator.stop();
                    setTimeout(() => startSimulator({ skipStartButton: true }), 150);
                }
            } catch (err) {
                showStatus('Ошибка загрузки карты: ' + (err.message || String(err)), 'error');
            }
        };
        reader.readAsText(file, 'UTF-8');
    }

    function initMapUpload() {
        const mapInput = document.getElementById('mapFileInput');
        const mapSection = document.getElementById('mapUploadSection');
        if (!mapInput || !mapSection) return;

        mapInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                handleMapFile(file);
                e.target.value = '';
            }
        });

        mapSection.addEventListener('dragover', (e) => {
            e.preventDefault();
            mapSection.classList.add('dragover');
        });

        mapSection.addEventListener('dragleave', () => {
            mapSection.classList.remove('dragover');
        });

        mapSection.addEventListener('drop', (e) => {
            e.preventDefault();
            mapSection.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) {
                handleMapFile(file);
                mapInput.value = '';
            }
        });
    }

    function initSimulatorButtons(noScriptMsg) {
        const btnSim = document.getElementById('btnSimulator');
        if (!btnSim) return;

        btnSim.addEventListener('click', () => {
            const scriptContent = getScriptContent();
            if (!scriptContent) {
                showStatus(noScriptMsg || 'Сначала загрузите Python скрипт', 'error');
                return;
            }
            if (!window.customMapData) {
                showStatus('Сначала выберите карту (.json) или загрузите её через редактор.', 'error');
                return;
            }
            const skipStart = !!window.mapJustSavedFromEditor;
            if (window.mapJustSavedFromEditor) window.mapJustSavedFromEditor = false;
            startSimulator({ skipStartButton: skipStart });
        });

        const btnReset = document.getElementById('btnReset');
        if (btnReset) {
            btnReset.addEventListener('click', () => {
                if (window.simulator) window.simulator.reset();
            });
        }

        const btnClose = document.getElementById('btnClose');
        if (btnClose) {
            btnClose.addEventListener('click', () => {
                const container = document.getElementById('simulatorContainer');
                if (container) container.classList.remove('show');
                const ph = document.getElementById('placeholderCenter');
                const mapOpen = document.getElementById('mapEditorContainer')?.classList.contains('show');
                if (ph && !mapOpen) ph.classList.remove('hide');
                const sensorPanel = document.getElementById('sensorPanel');
                if (sensorPanel) sensorPanel.innerHTML = '';
                if (window.simulator) window.simulator.stop();
            });
        }

        const btnRobotButton = document.getElementById('btnRobotButton');
        if (btnRobotButton) {
            const press = () => {
                if (window.currentBot && window.currentBot.button) {
                    if (typeof window.currentBot.button.pressButton === 'function') {
                        window.currentBot.button.pressButton();
                    } else {
                        window.currentBot.button._pressed = true;
                    }
                }
            };
            const release = () => {
                if (window.currentBot && window.currentBot.button) {
                    if (typeof window.currentBot.button.releaseButton === 'function') {
                        window.currentBot.button.releaseButton();
                    } else {
                        window.currentBot.button._pressed = false;
                    }
                }
            };
            btnRobotButton.addEventListener('mousedown', press);
            btnRobotButton.addEventListener('mouseup', release);
            btnRobotButton.addEventListener('mouseleave', release);
        }

        document.addEventListener('keydown', (e) => {
            if ((e.key === 'b' || e.key === 'B') && !e.ctrlKey && !e.metaKey) {
                if (window.currentBot && window.currentBot.button) {
                    if (typeof window.currentBot.button.pressButton === 'function') {
                        window.currentBot.button.pressButton();
                    } else {
                        window.currentBot.button._pressed = true;
                    }
                }
            }
        });
        document.addEventListener('keyup', (e) => {
            if (e.key === 'b' || e.key === 'B') {
                if (window.currentBot && window.currentBot.button) {
                    if (typeof window.currentBot.button.releaseButton === 'function') {
                        window.currentBot.button.releaseButton();
                    } else {
                        window.currentBot.button._pressed = false;
                    }
                }
            }
        });
    }

    async function startSimulator(options = {}) {
        const skipStartButton = options.skipStartButton || false;
        const scriptContent = getScriptContent();
        if (!scriptContent) {
            showStatus('Нет скрипта для запуска', 'error');
            return;
        }

        showStatus('Запуск симулятора... Загрузка Pyodide может занять время...', 'info');
        appendSimLog('INFO', 'Запуск симулятора, загрузка Pyodide/скрипта');

        const container = document.getElementById('simulatorContainer');
        if (container) container.classList.add('show');
        const ph = document.getElementById('placeholderCenter');
        if (ph) ph.classList.add('hide');
        const mapEditorContainer = document.getElementById('mapEditorContainer');
        if (mapEditorContainer) mapEditorContainer.classList.remove('show');

        const customMapData = window.customMapData || null;

        if (!window.simulator) {
            window.simulator = new RobotSimulator('simulatorCanvas', scriptContent, customMapData);
            while (!window.simulator.pyodideReady) {
                await new Promise(r => setTimeout(r, 100));
            }
        } else {
            window.simulator.loadScript(scriptContent);
            if (window.simulator.updateMap && customMapData) {
                window.simulator.updateMap(customMapData);
            }
            if (window.simulator.running) {
                window.simulator.stop();
                await new Promise(r => setTimeout(r, 100));
            }
        }

        window.currentBot = window.simulator.bot || null;
        if (window.simulator && typeof window.simulator.draw === 'function') {
            window.simulator.draw();
        }

        if (window.simulator) {
            window.simulator.start({ skipStartButton });
        }

        showStatus(skipStartButton ? 'Скрипт обновлён.' : 'Симулятор запущен. Нажмите «Старт» для начала.', 'success');
    }

    function initSimLogControls() {
        const clearBtn = document.getElementById('debug-log-clear');
        const box = document.getElementById('debug-log');
        if (clearBtn && box) {
            clearBtn.addEventListener('click', () => { box.textContent = ''; });
        }
    }

    window.SimulatorUI = {
        init: function(config) {
            getScriptContent = config.getScriptContent || (() => null);
            onMapLoaded = config.onMapLoaded || (() => {});
            updateSimulatorButton = config.updateSimulatorButton || (() => {});
            initSimLogControls();
            return {
                showStatus,
                handleMapFile,
                initMapUpload,
                initSimulatorButtons,
                startSimulator
            };
        }
    };
})();
