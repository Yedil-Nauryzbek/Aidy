import git
import os
import shutil
import stat

# -----------------------------
# Настройки
# -----------------------------
local_folder = r"C:\Users\yasin\OneDrive\Рабочий стол\Aidy-main (2)"
repo_url = "https://github.com/Yedil-Nauryzbek/Aidy.git"
new_branch = "new_settings_02"  # без пробелов
commit_message = "Add Aidy-main files"
tmp_repo_path = r"C:\Users\yasin\OneDrive\Рабочий стол\Aidy_temp_repo"

# -----------------------------
# Удаление защищённых файлов
# -----------------------------
def remove_readonly(func, path, excinfo):
    os.chmod(path, stat.S_IWRITE)
    func(path)

if os.path.exists(tmp_repo_path):
    shutil.rmtree(tmp_repo_path, onerror=remove_readonly)

# -----------------------------
# Клонируем репозиторий
# -----------------------------
print("Клонируем репозиторий...")
repo = git.Repo.clone_from(repo_url, tmp_repo_path)

# -----------------------------
# Создаём новую ветку
# -----------------------------
print(f"Создаём ветку {new_branch}...")
repo.git.checkout('HEAD', b=new_branch)

# -----------------------------
# Функция копирования с фильтром
# -----------------------------
def copy_filtered(src, dst):
    for item in os.listdir(src):
        if item in [".vs", ".git", "bin", "obj"]:
            continue  # пропускаем эти папки
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        try:
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        except Exception as e:
            print(f"Пропущено: {s} -> {e}")

# -----------------------------
# Копируем файлы
# -----------------------------
print("Копируем файлы...")
copy_filtered(local_folder, tmp_repo_path)

# -----------------------------
# Git add, commit, push
# -----------------------------
print("Добавляем файлы в git...")
repo.git.add(A=True)
repo.index.commit(commit_message)
origin = repo.remote(name='origin')
print("Пушим ветку на GitHub...")
origin.push(refspec=f"{new_branch}:{new_branch}")

print(f"Папка '{local_folder}' успешно загружена в ветку '{new_branch}'.")