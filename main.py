import flet as ft
import boto3
import json
import threading

# Файлы настроек (в облаке)
METADATA_FILE = ".folder_metadata.json"
SETTINGS_FILE = ".app_settings.json"

def get_file_style(filename):
    parts = filename.rsplit('.', 1)
    ext = ('.' + parts[1].lower()) if len(parts) > 1 else ''
    
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
        return ft.icons.IMAGE, ft.colors.PURPLE_400
    elif ext in ['.mp3', '.wav', '.ogg']:
        return ft.icons.AUDIO_FILE, ft.colors.PINK_400
    elif ext in ['.mp4', '.mov', '.avi', '.mkv']:
        return ft.icons.VIDEO_FILE, ft.colors.RED_400
    elif ext in ['.pdf']:
        return ft.icons.PICTURE_AS_PDF, ft.colors.RED_700
    elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz']:
        return ft.icons.FOLDER_ZIP, ft.colors.AMBER_600
    elif ext in ['.py', '.js', '.html', '.css', '.json', '.xml', '.dart']:
        return ft.icons.CODE, ft.colors.CYAN_600
    elif ext in ['.txt', '.doc', '.docx', '.xls', '.xlsx', '.ppt']:
        return ft.icons.DESCRIPTION, ft.colors.BLUE_400
    elif ext in ['.exe', '.apk', '.msi']:
        return ft.icons.ANDROID, ft.colors.GREEN_400
    else:
        return ft.icons.INSERT_DRIVE_FILE, ft.colors.GREY_400

class S3CloudApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Личное Облако"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(color_scheme_seed=ft.colors.BLUE)
        
        # S3 State
        self.s3 = None
        self.bucket_name = None
        
        # App State
        self.metadata = {}
        self.app_settings = {"default_folder": ""}
        self.current_path = ""
        self.is_mobile = page.platform in [ft.PagePlatform.ANDROID, ft.PagePlatform.IOS]

        # UI Components
        self.file_picker = ft.FilePicker(on_result=self.upload_files_result)
        self.page.overlay.append(self.file_picker)
        
        # 1. Строим UI (все скрыто)
        self.build_ui()
        
        # 2. Проверяем авто-вход
        if self.page.client_storage.contains_key("s3_creds"):
            self.try_auto_login()
        else:
            self.show_login_screen()

    # --- AUTH & LOGIN ---

    def try_auto_login(self):
        try:
            creds = self.page.client_storage.get("s3_creds")
            if creds:
                region = creds.get('region', 'ru-central-1')
                self.connect_s3(creds['access_key'], creds['secret_key'], creds['endpoint'], creds['bucket'], region)
            else:
                self.show_login_screen()
        except Exception:
            self.show_login_screen()

    def connect_s3(self, ak, sk, endpoint, bucket, region='ru-central-1'):
        try:
            self.s3 = boto3.client(
                's3',
                endpoint_url=endpoint,
                aws_access_key_id=ak,
                aws_secret_access_key=sk,
                region_name=region
            )
            self.bucket_name = bucket
            
            # Тестовый запрос
            self.s3.list_objects_v2(Bucket=self.bucket_name, MaxKeys=1)
            
            # Сохраняем (включая регион)
            self.page.client_storage.set("s3_creds", {
                'access_key': ak, 
                'secret_key': sk, 
                'endpoint': endpoint, 
                'bucket': bucket,
                'region': region
            })

            # Загрузка
            self.load_metadata()
            self.load_app_settings()
            self.current_path = self.app_settings.get("default_folder", "")
            
            self.show_main_screen()
            self.refresh_file_list()
            self.show_snack("Вход выполнен")

        except Exception as e:
            self.s3 = None
            self.show_snack(f"Ошибка входа: {e}", color="red")
            self.show_login_screen()

    def logout(self, e=None):
        self.s3 = None
        self.bucket_name = None
        self.metadata = {}
        self.app_settings = {}
        self.current_path = ""
        
        # Удаляем ключи из хранилища
        self.page.client_storage.remove("s3_creds")
            
        self.grid.controls.clear()
        self.storage_text.value = ""
        self.path_text.value = "Не авторизован"
        self.fab.visible = False
        
        self.show_login_screen()
        self.show_snack("Вы вышли из системы")

    # --- UI BUILDING ---

    def build_ui(self):
        # Header
        self.storage_text = ft.Text("", color=ft.colors.ORANGE, weight=ft.FontWeight.BOLD, size=12)
        
        # Menu
        self.popup_menu = ft.PopupMenuButton(
            items=[
                ft.PopupMenuItem(text="Настройки", icon=ft.icons.SETTINGS, on_click=self.show_global_settings),
                ft.PopupMenuItem(text="Цвет темы", icon=ft.icons.COLOR_LENS, on_click=self.show_theme_picker),
                ft.PopupMenuItem(text="Выход", icon=ft.icons.LOGOUT, on_click=self.logout),
            ]
        )

        self.app_bar = ft.AppBar(
            title=ft.Column([
                ft.Text("Мое Облако", size=18),
                ft.Row([ft.Icon(ft.icons.CLOUD_QUEUE, size=14, color=ft.colors.ORANGE), self.storage_text], spacing=5)
            ], spacing=2),
            actions=[
                ft.IconButton(ft.icons.REFRESH, on_click=lambda e: self.refresh_file_list(), tooltip="Обновить"),
                self.popup_menu
            ],
            visible=False # Изначально скрыт
        )
        self.page.appbar = self.app_bar

        # Path & Back
        self.path_text = ft.Text("Загрузка...", size=16, weight=ft.FontWeight.BOLD)
        self.back_btn = ft.IconButton(ft.icons.ARROW_BACK, on_click=self.go_back, disabled=True)
        
        # File Grid
        self.grid = ft.GridView(
            expand=1,
            runs_count=5,
            max_extent=150,
            child_aspect_ratio=0.8,
            spacing=10,
            run_spacing=10,
        )

        # FAB
        self.fab = ft.FloatingActionButton(
            icon=ft.icons.ADD,
            on_click=self.show_add_menu,
            visible=False
        )
        self.page.floating_action_button = self.fab

        # Containers
        self.main_container = ft.Column(
            [
                ft.Row([self.back_btn, self.path_text], alignment=ft.MainAxisAlignment.START),
                ft.Divider(),
                self.grid
            ],
            expand=True,
            visible=False
        )
        
        self.login_container = ft.Column(
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
            visible=True
        )

        self.page.add(self.main_container, self.login_container)

    def show_login_screen(self):
        # Безопасное переключение
        if self.page.appbar: self.page.appbar.visible = False
        self.main_container.visible = False
        self.fab.visible = False
        self.login_container.visible = True
        
        # Поля
        endpoint_tf = ft.TextField(label="Endpoint URL", value="https://s3.cloud.ru", width=300)
        region_tf = ft.TextField(label="Region Name", value="ru-central-1", width=300)
        bucket_tf = ft.TextField(label="Bucket Name", value="s3-cloud-storage", width=300)
        ak_tf = ft.TextField(label="Access Key ID", width=300, password=True, can_reveal_password=True)
        sk_tf = ft.TextField(label="Secret Access Key", width=300, password=True, can_reveal_password=True)
        
        def login_click(e):
            if not all([endpoint_tf.value, bucket_tf.value, ak_tf.value, sk_tf.value]):
                self.show_snack("Заполните основные поля", color="red")
                return
            self.connect_s3(ak_tf.value, sk_tf.value, endpoint_tf.value, bucket_tf.value, region_tf.value)

        self.login_container.controls = [
            ft.Icon(ft.icons.CLOUD_CIRCLE, size=100, color=ft.colors.BLUE),
            ft.Text("Вход в S3 Облако", size=24, weight=ft.FontWeight.BOLD),
            ft.Container(height=20),
            endpoint_tf, region_tf, bucket_tf, ak_tf, sk_tf,
            ft.Container(height=20),
            ft.ElevatedButton("Войти", on_click=login_click, width=200, height=50)
        ]
        self.page.update()

    def show_main_screen(self):
        self.login_container.visible = False
        if self.page.appbar: self.page.appbar.visible = True
        self.main_container.visible = True
        self.fab.visible = True
        self.page.update()

    # --- S3 LOGIC ---

    def refresh_file_list(self):
        if not self.s3: return

        self.grid.controls.clear()
        self.back_btn.disabled = (self.current_path == "")
        display_path = self.current_path if self.current_path else "/"
        self.path_text.value = f"Путь: {display_path}"
        
        try:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket_name, Prefix=self.current_path, Delimiter='/'
            )
            
            if 'CommonPrefixes' in response:
                for prefix in response['CommonPrefixes']:
                    folder_key = prefix['Prefix']
                    folder_name = folder_key.rstrip('/').split('/')[-1]
                    meta = self.metadata.get(folder_key, {})
                    self.grid.controls.append(
                        self.create_folder_item(folder_name, folder_key, meta.get("color", ft.colors.BLUE), meta.get("caption", folder_name))
                    )

            if 'Contents' in response:
                for obj in response['Contents']:
                    key = obj['Key']
                    if key == self.current_path or key in [METADATA_FILE, SETTINGS_FILE]: 
                        continue 
                    filename = key.split('/')[-1]
                    if filename:
                        self.grid.controls.append(self.create_file_item(filename, key, obj['Size']))

            self.update_storage_usage()

        except Exception as e:
            self.show_snack(f"Ошибка S3: {e}", color="red")
        
        self.page.update()

    def upload_files_result(self, e):
        if not self.s3: return
        
        if e.files:
            def upload_in_background():
                for f in e.files:
                    try:
                        self.s3.upload_file(f.path, self.bucket_name, self.current_path + f.name)
                        self.show_snack(f"Загружен: {f.name}")
                    except Exception as ex: 
                        self.show_snack(f"Ошибка загрузки {f.name}: {ex}", color="red")
                self.refresh_file_list()
            
            threading.Thread(target=upload_in_background, daemon=True).start()

    def delete_folder(self, folder_key):
        if not self.s3: return

        def delete_in_background():
            try:
                paginator = self.s3.get_paginator('list_objects_v2')
                objects = []
                for page in paginator.paginate(Bucket=self.bucket_name, Prefix=folder_key):
                    if 'Contents' in page:
                        objects.extend([{'Key': o['Key']} for o in page['Contents']])
                
                if objects:
                    for i in range(0, len(objects), 1000):
                        self.s3.delete_objects(Bucket=self.bucket_name, Delete={'Objects': objects[i:i+1000]})
                
                if folder_key in self.metadata:
                    del self.metadata[folder_key]
                    self.save_metadata()
                    
                self.show_snack("Папка удалена")
                self.refresh_file_list()
            except Exception as e:
                self.show_snack(f"Ошибка удаления: {e}", color="red")
        
        threading.Thread(target=delete_in_background, daemon=True).start()

    # --- HELPERS ---

    def load_metadata(self):
        if not self.s3: return
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=METADATA_FILE)
            self.metadata = json.loads(response['Body'].read().decode('utf-8'))
        except Exception:
            self.metadata = {}

    def save_metadata(self):
        if not self.s3: return
        try:
            self.s3.put_object(Bucket=self.bucket_name, Key=METADATA_FILE, Body=json.dumps(self.metadata))
        except Exception: pass

    def load_app_settings(self):
        if not self.s3: return
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=SETTINGS_FILE)
            self.app_settings = json.loads(response['Body'].read().decode('utf-8'))
        except Exception:
            self.app_settings = {"default_folder": ""}

    def save_app_settings(self):
        if not self.s3: return
        try:
            self.s3.put_object(Bucket=self.bucket_name, Key=SETTINGS_FILE, Body=json.dumps(self.app_settings))
        except Exception as e:
            self.show_snack(f"Ошибка сохранения настроек: {e}", color="red")

    def update_storage_usage(self):
        if not self.s3: return
        def calculate_in_background():
            try:
                total = 0
                paginator = self.s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=self.bucket_name):
                    if 'Contents' in page:
                        for obj in page['Contents']: total += obj['Size']
                
                if total < 1024 * 1024: display = f"{total/1024:.1f} KB"
                elif total < 1024 * 1024 * 1024: display = f"{total/(1024*1024):.1f} MB"
                else: display = f"{total/(1024*1024*1024):.2f} GB"
                
                self.storage_text.value = display
                self.storage_text.update()
            except: pass
        threading.Thread(target=calculate_in_background, daemon=True).start()

    # --- DIALOGS ---

    def show_add_menu(self, e):
        if not self.s3: return
        dlg = ft.AlertDialog(
            title=ft.Text("Создать"),
            content=ft.Column([
                ft.ElevatedButton("Загрузить файлы", icon=ft.icons.UPLOAD, 
                    on_click=lambda _: [self.close_dialog(dlg), self.file_picker.pick_files(allow_multiple=True)]),
                ft.ElevatedButton("Новая папка", icon=ft.icons.CREATE_NEW_FOLDER, 
                    on_click=lambda _: [self.close_dialog(dlg), self.show_create_folder_dialog()])
            ], height=120, tight=True)
        )
        self.open_dialog(dlg)

    def show_create_folder_dialog(self):
        tf = ft.TextField(label="Имя папки")
        def create_action(e):
            if tf.value and self.s3:
                try:
                    self.s3.put_object(Bucket=self.bucket_name, Key=self.current_path + tf.value + "/")
                    self.close_dialog(dlg)
                    self.refresh_file_list()
                except Exception as ex: self.show_snack(f"Ошибка: {ex}", color="red")
        
        dlg = ft.AlertDialog(title=ft.Text("Новая папка"), content=tf, actions=[
            ft.TextButton("Отмена", on_click=lambda e: self.close_dialog(dlg)),
            ft.ElevatedButton("Создать", on_click=create_action)
        ])
        self.open_dialog(dlg)

    def fetch_all_folders(self):
        if not self.s3: return []
        folders = ["/ (Корень)"]
        try:
            paginator = self.s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket_name):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.endswith('/') and key != '/': folders.append(key)
        except: pass
        return folders

    def open_folder_settings(self, folder_key, name, current_color, current_caption):
        if not self.s3: return
        tf = ft.TextField(label="Имя", value=current_caption)
        colors_map = {
            "Синий": ft.colors.BLUE, "Красный": ft.colors.RED, "Зеленый": ft.colors.GREEN, 
            "Оранжевый": ft.colors.ORANGE, "Фиолетовый": ft.colors.PURPLE, 
            "Серый": ft.colors.GREY, "Желтый": ft.colors.YELLOW
        }
        curr_col_name = next((k for k,v in colors_map.items() if v == current_color), "Синий")
        dd = ft.Dropdown(label="Цвет", options=[ft.dropdown.Option(k) for k in colors_map], value=curr_col_name)

        def save(e):
            self.metadata[folder_key] = {"color": colors_map.get(dd.value), "caption": tf.value}
            self.save_metadata()
            self.close_dialog(dlg)
            self.refresh_file_list()
        
        def delete(e):
            self.close_dialog(dlg)
            self.delete_folder(folder_key)

        dlg = ft.AlertDialog(title=ft.Text(name), content=ft.Column([tf, dd], height=150), actions=[
            ft.TextButton("УДАЛИТЬ", style=ft.ButtonStyle(color="red"), on_click=delete),
            ft.ElevatedButton("Сохранить", on_click=save)
        ])
        self.open_dialog(dlg)

    def show_file_actions(self, key, filename):
        if not self.s3: return
        def delete(e):
            try:
                self.s3.delete_object(Bucket=self.bucket_name, Key=key)
                self.close_dialog(dlg)
                self.refresh_file_list()
            except Exception as ex: self.show_snack(f"Ошибка: {ex}", color="red")
        
        dlg = ft.AlertDialog(title=ft.Text(filename), actions=[
            ft.ElevatedButton("Удалить", color="red", on_click=delete)
        ])
        self.open_dialog(dlg)

    def show_global_settings(self, e):
        if not self.s3: return
        folders = self.fetch_all_folders()
        dd_start = ft.Dropdown(
            label="Папка при запуске",
            options=[ft.dropdown.Option(text=f, key=f if f != "/ (Корень)" else "") for f in folders],
            value=self.app_settings.get("default_folder", "")
        )
        def save_settings(e):
            self.app_settings["default_folder"] = dd_start.value
            self.save_app_settings()
            self.close_dialog(dlg)
            self.show_snack("Настройки сохранены")

        dlg = ft.AlertDialog(title=ft.Text("Настройки"), content=dd_start, actions=[
            ft.ElevatedButton("Сохранить", on_click=save_settings)
        ])
        self.open_dialog(dlg)

    def show_theme_picker(self, e):
        colors = [
            ft.colors.BLUE, ft.colors.LIGHT_BLUE, ft.colors.CYAN, ft.colors.TEAL,
            ft.colors.GREEN, ft.colors.LIGHT_GREEN, ft.colors.LIME, ft.colors.YELLOW,
            ft.colors.AMBER, ft.colors.ORANGE, ft.colors.DEEP_ORANGE, ft.colors.RED,
            ft.colors.PINK, ft.colors.PURPLE, ft.colors.DEEP_PURPLE, ft.colors.INDIGO,
            ft.colors.BLUE_GREY, ft.colors.BROWN, ft.colors.GREY
        ]
        grid = ft.GridView(runs_count=5, height=300, padding=10, spacing=10)
        for c in colors:
            grid.controls.append(ft.Container(
                bgcolor=c, width=40, height=40, border_radius=20, border=ft.border.all(1, ft.colors.OUTLINE),
                on_click=lambda e, col=c: [setattr(self.page.theme, 'color_scheme_seed', col), self.page.update(), self.close_dialog(dlg)],
                ink=True
            ))
        dlg = ft.AlertDialog(title=ft.Text("Выберите цвет"), content=grid)
        self.open_dialog(dlg)

    # --- NAVIGATION & DOWNLOAD ---

    def navigate_to(self, folder_key):
        if not self.s3: return
        self.current_path = folder_key
        self.refresh_file_list()

    def go_back(self, e):
        if not self.s3: return
        parts = self.current_path.rstrip('/').split('/')
        if len(parts) > 1: self.current_path = "/".join(parts[:-1]) + "/"
        else: self.current_path = ""
        self.refresh_file_list()

    def download_file(self, key, filename):
        if not self.s3: return
        def generate_and_launch():
            try:
                url = self.s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': self.bucket_name, 'Key': key, 'ResponseContentDisposition': f'attachment; filename="{filename}"'},
                    ExpiresIn=3600
                )
                self.page.launch_url(url)
                self.show_snack(f"Скачивание: {filename}")
            except Exception as e:
                self.show_snack(f"Ошибка ссылки: {e}", color="red")
        threading.Thread(target=generate_and_launch, daemon=True).start()

    # --- FACTORIES ---

    def create_folder_item(self, name, full_key, color, caption):
        return ft.Container(
            content=ft.Column([
                ft.Icon(ft.icons.FOLDER, size=50, color=color),
                ft.Text(caption, text_align=ft.TextAlign.CENTER, overflow=ft.TextOverflow.ELLIPSIS, size=12),
            ], alignment=ft.MainAxisAlignment.CENTER),
            ink=True,
            on_click=lambda e: self.navigate_to(full_key),
            border_radius=10, padding=10,
            on_long_press=lambda e: self.open_folder_settings(full_key, name, color, caption)
        )

    def create_file_item(self, name, full_key, size):
        icon, color = get_file_style(name)
        return ft.Container(
            content=ft.Column([
                ft.Icon(icon, size=50, color=color),
                ft.Text(name, text_align=ft.TextAlign.CENTER, overflow=ft.TextOverflow.ELLIPSIS, size=12, weight=ft.FontWeight.W_500),
                ft.Text(f"{size/1024:.1f} KB", size=10, color=ft.colors.GREY),
            ], alignment=ft.MainAxisAlignment.CENTER),
            ink=True,
            on_click=lambda e: self.download_file(full_key, name),
            on_long_press=lambda e: self.show_file_actions(full_key, name),
            border_radius=10, padding=10,
        )

    # --- UTILS ---

    def open_dialog(self, dlg):
        self.page.dialog = dlg
        dlg.open = True
        self.page.update()

    def close_dialog(self, dlg):
        dlg.open = False
        self.page.update()

    def show_snack(self, message, color="green"):
        self.page.snack_bar = ft.SnackBar(content=ft.Text(message, color="white"), bgcolor=color)
        self.page.snack_bar.open = True
        self.page.update()

def main(page: ft.Page):
    app = S3CloudApp(page)

if __name__ == "__main__":
    ft.app(target=main)
