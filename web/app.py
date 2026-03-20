import os
# Must be before any TF import
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS', '0')
import logging
import shutil
import zipfile
from flask import Flask, request, send_file, jsonify

# Avoid "I/O operation on closed file" from werkzeug logger
logging.getLogger('werkzeug').setLevel(logging.WARNING)
from werkzeug.utils import secure_filename
import train  # Наш скрипт обучения

app = Flask(__name__, static_folder='static', static_url_path='')

UPLOAD_FOLDER = 'dataset'
MODEL_FILE = 'model.tflite'

def log(msg, level='INFO'):
    """Log to stdout (visible in docker logs)."""
    line = f"[Robot:{level}] {msg}"
    print(line, flush=True)

def clear_directory(folder):
    """Clears contents of a directory without removing the directory itself (which might be a mount point)."""
    if not os.path.exists(folder):
        os.makedirs(folder)
        return

    for filename in os.listdir(folder):
        file_path = os.path.join(folder, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            log(f'Failed to delete {file_path}: {e}', 'WARN')

# Clean dataset folder on startup
clear_directory(UPLOAD_FOLDER)

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/validate-script', methods=['POST'])
def validate_script():
    """Проверка скрипта перед загрузкой: синтаксис и наличие run_robot."""
    try:
        j = request.get_json(silent=True) or {}
        script = j.get('script', '')
        if not script or not isinstance(script, str):
            return jsonify({'valid': False, 'error': 'Скрипт пуст'})
        import ast
        try:
            tree = ast.parse(script)
        except SyntaxError as e:
            return jsonify({'valid': False, 'error': f'Синтаксическая ошибка (строка {e.lineno}): {e.msg}'})
        has_run_robot = any(
            isinstance(node, ast.FunctionDef) and node.name == 'run_robot'
            for node in ast.walk(tree)
        )
        if not has_run_robot:
            return jsonify({'valid': False, 'error': 'Функция def run_robot(bot): не найдена'})
        return jsonify({'valid': True})
    except Exception as e:
        log(str(e), 'ERR')
        return jsonify({'valid': False, 'error': str(e)})

@app.route('/api/log', methods=['POST'])
def api_log():
    """Receive client logs — visible in docker logs."""
    try:
        j = request.get_json(silent=True) or {}
        level = j.get('level', 'INFO')
        msg = j.get('msg', str(j))
        log(msg, level)
        return jsonify({'ok': True})
    except Exception as e:
        log(str(e), 'ERR')
        return jsonify({'error': str(e)}), 500

@app.route('/train', methods=['POST'])
def upload_and_train():
    try:
        # 1. Receive ZIP file
        if 'dataset' not in request.files:
            return jsonify({'error': 'No dataset uploaded'}), 400
        
        file = request.files['dataset']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # 2. Clear old data
        clear_directory(UPLOAD_FOLDER)

        # 3. Save and unzip
        zip_path = os.path.join(UPLOAD_FOLDER, 'dataset.zip')
        file.save(zip_path)
        
        log("Unzipping dataset...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(UPLOAD_FOLDER)
        
        os.remove(zip_path)

        log("Starting training...")
        train.DATASET_DIR = UPLOAD_FOLDER # Переопределяем путь
        train.MODEL_FILENAME = MODEL_FILE
        
        # Запускаем main логику обучения
        train.run_training()

        log("Training done, sending model")
        return send_file(MODEL_FILE, as_attachment=True)

    except Exception as e:
        log(f"Error: {e}", 'ERR')
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8085)
