# -*- coding: utf-8 -*-

from configs import *
from bot.app_configs import host,Thread


root_dir = os.path.dirname(__file__)
db_file = os.path.join(root_dir,'database.db')

TASK_SCHEDULES = {}


class Utils:
	@staticmethod
	def load_proxies():
		proxies = []
		file = os.path.join(root_dir,'universals','proxies.txt')
		with open(file,"r") as f:
			for proxy in f.readlines():
				proxy = proxy.replace("\n","").split(":")
				ip = proxy[0]
				port = proxy[1]
				username = proxy[2]
				password = proxy[3] if len(proxy) > 3 else None
				if password is not None:
					proxy = {
						"http": f'http://{username}:{password}@{ip}:{port}',
						"https": f'http://{username}:{password}@{ip}:{port}'
					}
				else:
					proxy = {
						"http": f'http://{username}:@{ip}:{port}',
						"https": f'http://{username}:@{ip}:{port}'
					}
					
				proxies.append(proxy)

		if os.getenv('MITMWEB', '1').strip().lower() not in ('0', 'false', 'no'):
			return [{
				'http': 'http://127.0.0.1:8080',
				'https': 'http://127.0.0.1:8080'
			}]
		return proxies
	
	@staticmethod
	def get_proxy_cert(proxy_cert):
		return os.path.join(root_dir,proxy_cert)

	@staticmethod
	def format_proxy(proxies):
		if isinstance(proxies,dict):
			return proxies['http']
		elif isinstance(proxies,str) and 'http://' in proxies:
			return {
				'http':proxies,
				'https':proxies
			}
		return None

	@staticmethod
	def generate_android_version():
		major_version = random.randint(2, 10)
		minor_version = random.randint(0, 9)
		build_version = random.randint(0, 9999)
		return f"{major_version}.{minor_version}.{build_version}"
	
	@staticmethod
	def generate_android_device():
		devices = [
				"Samsung Galaxy S21",
				"Samsung Galaxy S6",
				"Samsung Galaxy S5",
				"Samsung Galaxy S7",
				"Samsung Galaxy S8",
				"Samsung Galaxy S8+",
				"Samsung Galaxy S9",
				"Samsung Galaxy S9+",
				"Samsung Galaxy S10",
				"Samsung Galaxy S20",
				"Samsung Galaxy Note8",
				"Samsung Galaxy Note9",
				"Samsung Galaxy Note8+",
				"Samsung Galaxy Note9+",
				"Samsung Galaxy Note10",
				"Samsung Galaxy Note10+",
				"Google Pixel 5",
				"Google Pixel 4",
				"Google Pixel 3",
				"OnePlus 9 Pro",
				"OnePlus 8T",
				"OnePlus 8",
				"Sony Xperia 1 III",
				"Sony Xperia 5 II",
				"Sony Xperia 10 III",
				"Motorola Edge+",
				"Motorola Razr",
				"Xiaomi Mi 11",
				"Xiaomi Mi 10",
				"Xiaomi Redmi Note 10",
				"Nokia 8.3",
				"Nokia 5.4",
				"Huawei Mate 40 Pro",
				"Huawei P40 Pro",
				"LG Wing",
				"LG Velvet",
		]
		return random.choice(devices)
	
	@staticmethod
	def generate_user_agent(device, count):
		if str(device).lower() == 'android':
			devices = Utils.generate_android_device()
			devices = devices * (count // len(devices)) + devices[:count % len(devices)]
			browsers = [
				{"name":"Chrome","version":f"{random.choice([90,120])}.0.{random.choice([0,4430])}.{random.choice([0,210])}"},
				{"name":"Firefox","version":f"{random.choice([90,121])}.0.{random.choice([0,4430])}.{random.choice([0,210])}"},
			]
			browser = random.choice(browsers)
			return f"Mozilla/5.0 (Linux; Android {Utils.generate_android_version()}; {Utils.generate_android_device()}) AppleWebKit/537.36 (KHTML, like Gecko) {browser['name']}/{browser['version']} Mobile Safari/537.36"
		else:
			return UserAgent(use_external_data=True)
	
	@staticmethod
	def create_tables():
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS admins (
					id TEXT PRIMARY KEY,
					email TEXT,
				  	password TEXT,
				 	plain_password TEXT,
				  	role TEXT,
				  	status TEXT,
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP )''')
			
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS creators (
					id TEXT PRIMARY KEY,
				  	email TEXT,
					data TEXT,
				  	admin TEXT,
				  	category TEXT DEFAULT 'creator',
				  	task_id TEXT,
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP )''')
			
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS tasks (
					id TEXT PRIMARY KEY,
					status TEXT NOT NULL,
				  	admin TEXT NOT NULL,
					action_count NOT NULL,
					message TEXT NULL,
				  	type TEXT NOT NULL,
				  	config TEXT NULL DEFAULT '{}',
				  	current_day INTEGER NOT NULL DEFAULT 1,
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP )''')
			
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS messages (
					id TEXT PRIMARY KEY,
					admin TEXT,
					creator_id TEXT,
					creator_name TEXT,
					recipient_id TEXT,
					recipient_name TEXT,
					has_media INTEGER DEFAULT 0,
					link TEXT,
					sender_status TEXT,
					caption TEXT,
					price REAL DEFAULT 0,
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
				)''')
			
			cursor.execute('''
				CREATE TABLE IF NOT EXISTS users (
					id TEXT PRIMARY KEY,
					admin TEXT,
					username TEXT,
					commented_at TEXT,
					task_id TEXT,
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
					status TEXT DEFAULT 'active',
					spent INTEGER DEFAULT 0,
					category TEXT DEFAULT 'scraped'
				)''')
			
			conn.commit()
			
			success,msg = True, 'Tables created'
		except Exception as error:
			success,msg = False, error
		finally:
			conn.close()
			return success,msg

	
	@staticmethod
	def add_admin(admin_id,admin_data):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			email = admin_data['email']
			password = admin_data['password']
			plain_password = admin_data['plain_password']
			role = admin_data['role']
			status = admin_data['status']

			cursor.execute("""INSERT INTO admins 
				  (id, email, password, plain_password, role, status) 
				  VALUES (?, ?, ?, ?, ?, ?)""", 
				  (admin_id,email,password,plain_password,role,status))
			conn.commit()
			
			success,msg = True, 'Admin added successfully'
		except Exception as error:
			success,msg =  False, str(f'Error adding admin :{error}')
		finally:
			conn.close()
			return success,msg

	@staticmethod
	def delete_admin(admin_id):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:

			cursor.execute('DELETE FROM admins WHERE id = ?', (admin_id,))
			conn.commit()

			success,msg =  True,'Admin deleted successfully'

		except Exception as error:
			success,msg = False,str(error)
		finally:
			conn.close()
			return success,msg

	@staticmethod
	def update_admin(admin_id, admin_data):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			email = admin_data['email']
			password = admin_data['password']
			plain_password = admin_data['plain_password']
			role = admin_data['role']
			status = admin_data['status']

			
			cursor.execute(""" UPDATE admins 
				  SET email = ?, 
				  password = ?,
				  plain_password = ?,
				  role = ?,
				  status = ? WHERE id = ?""", 
				  (email,password,plain_password,role,status,admin_id))
			
			conn.commit()
			
			success,msg = True, 'Creator updated successfully'
		except Exception as error:
			success,msg = False, f'Error updating creator: {error}'
		finally:
			conn.close() 
			return success,msg   
		
	@staticmethod
	def get_admins(limit=20, offset=0,multiple=True,admin=None,keyword=None):
		success, admins, total_admins = True, [], 0
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:

			if multiple:
				cursor.execute("SELECT COUNT(*) FROM admins")
				total_admins = cursor.fetchone()[0]

				cursor.execute("SELECT * FROM admins ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
				rows = cursor.fetchall()

				admins = [{
					'id': row[0], 
					'email': row[1],
					'password':row[2],
					'plain_password':row[3],
					'role':row[4],
					'status':row[5],
					'created_at': row[6]
					} for row in rows]
			else:
				cursor.execute(f"SELECT * FROM admins WHERE {keyword} = ?", (admin,))
				row = cursor.fetchone()
				if row is None:raise Exception('admin not found')
				
				admins = {
					'id': row[0], 
					'email': row[1],
					'password':row[2],
					'plain_password':row[3],
					'role':row[4],
					'status':row[5],
					'created_at': row[6]
				}

		except Exception as error:
			success, admins = False, f'Error getting admins:{error}'

		finally:
			conn.close()
			return success, admins, total_admins


	@staticmethod
	def add_creator(creator_id,creator_email,creator_data,admin,category='creators',task_id=None):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			if len(creator_data.items()) > 0:
				category = 'creator' if category == 'creators' else 'user'
				cursor.execute("INSERT INTO creators (id, email, data, admin, category, task_id) VALUES (?, ?, ?, ?, ?, ?)", (creator_id,creator_email,json.dumps(creator_data),admin,category,task_id))
				conn.commit()
			
			success,msg = True, 'Creator added successfully'
		except Exception as error:
			success,msg =  False, str(f'Error adding creator :{error}')
		finally:
			conn.close()
			return success,msg

	@staticmethod
	def delete_creator(creator_id):
		success,msg = False	,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:

			cursor.execute('DELETE FROM creators WHERE id = ?', (creator_id,))
			conn.commit()

			success,msg =  True,'Creator deleted successfully'

		except Exception as error:
			success,msg = False,str(error)
		finally:
			conn.close()
			return success,msg

	@staticmethod
	def update_creator(creator_id,creator_email,creator_data):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			
			cursor.execute("UPDATE creators SET email = ?, data = ? WHERE id = ?", (creator_email,json.dumps(creator_data),creator_id))
			conn.commit()
			
			success,msg = True, 'Creator updated successfully'
		except Exception as error:
			success,msg = False, f'Error updating creator: {error}'
		finally:
			conn.close() 
			return success,msg

	@staticmethod
	def reset_creator_offsets(admin, category='creators'):
		success, msg, reset = False, '', 0
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			category = {'users': 'user', 'creators': 'creator', 'creator': 'creator'}.get(category, 'creator')
			cursor.execute(
				"SELECT COUNT(*) FROM creators WHERE admin = ? AND category = ?",
				(admin, category)
			)
			reset = cursor.fetchone()[0] or 0
			if reset < 1:
				success, msg = True, 'No creators to reset'
			else:
				try:
					cursor.execute(
						"""UPDATE creators
						SET data = json_set(COALESCE(data, '{}'), '$.message_offset', 0)
						WHERE admin = ? AND category = ? AND json_valid(COALESCE(data, '{}'))""",
						(admin, category)
					)
				except sqlite3.OperationalError:
					cursor.execute(
						"SELECT id, email, data FROM creators WHERE admin = ? AND category = ?",
						(admin, category)
					)
					rows = cursor.fetchall()
					reset = 0
					for creator_id, email, data in rows:
						payload = json.loads(data) if data else {}
						if not isinstance(payload, dict):
							payload = {}
						payload['message_offset'] = 0
						cursor.execute(
							"UPDATE creators SET data = ? WHERE id = ?",
							(json.dumps(payload), creator_id)
						)
						reset += 1
				conn.commit()
				label = 'creator' if reset == 1 else 'creators'
				success, msg = True, f'Reset offset for {reset} {label}'
		except Exception as error:
			success, msg = False, f'Error resetting creator offsets: {error}'
		finally:
			conn.close()
			return success, msg, reset
		
	@staticmethod
	def get_creators(admin='', limit=20, offset=0, multiple=True, creator=None, category='creator', constraint=None, keyword=None, selected_creators=[], exclude_from_posts=None):
		"""
		Fetch creators with various filters and options.
		
		Parameters:
			exclude_from_posts: str (optional) - The column name to exclude creators from (e.g., 'like_user_ids', 'comment_user_ids').
		"""
		success, creators, total_creators = False, [], 0
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			category = {'users':'user','creators':'creator','creator':'creator'}[category]
			
			if exclude_from_posts is not None:  # Ensure it's not empty
				placeholders = ','.join('?' * len(exclude_from_posts))  # Create (?, ?, ?) dynamically
				Utils.write_log(f'Exclude from posts {placeholders}')
				query = f"""
					SELECT COUNT(*) 
					FROM creators 
					WHERE id NOT IN ({placeholders}) 
					AND admin = ? 
					AND category = ?
				"""
				cursor.execute(query, (*exclude_from_posts, admin, category))  # Unpack list into query
				total_creators = cursor.fetchone()[0]

				query = f"""
					SELECT * 
					FROM creators 
					WHERE id NOT IN ({placeholders}) 
					AND admin = ? AND category = ?
					ORDER BY created_at DESC 
					LIMIT ? OFFSET ?
				"""
				cursor.execute(query, (*exclude_from_posts, admin, category, limit, offset))
				rows = cursor.fetchall()

				creators = [{
					'id': row[0], 
					'email': row[1],
					'data': json.loads(row[2]),
					'created_at': row[6]
				} for row in rows]
			elif selected_creators:  # Check if selected_creators is not empty
				placeholders = ','.join('?' for _ in selected_creators)  # Create placeholders for the IN clause
				cursor.execute(
					f"SELECT COUNT(*) FROM creators WHERE id IN ({placeholders}) AND admin = ?",
					(*selected_creators, admin)
				)
				total_creators = cursor.fetchone()[0]

				cursor.execute(
					f"""SELECT * FROM creators 
					WHERE id IN ({placeholders}) AND admin = ? 
					ORDER BY created_at DESC 
					LIMIT ? OFFSET ?""",
					(*selected_creators, admin, limit, offset)
				)
				rows = cursor.fetchall()

				creators = [{
					'id': row[0], 
					'email': row[1],
					'data': json.loads(row[2]),
					'created_at': row[6]
				} for row in rows]
			elif constraint is not None and keyword is not None:
				# If constraint and keyword are provided, filter by them
				cursor.execute(f"SELECT COUNT(*) FROM creators WHERE {constraint} = ? AND admin = ?", (keyword, admin))
				total_creators = cursor.fetchone()[0]

				cursor.execute(
					f"""SELECT * FROM creators 
					WHERE {constraint} = ? AND admin = ?
					ORDER BY created_at DESC 
					LIMIT ? OFFSET ?""",
					(keyword, admin, limit, offset)
				)
				rows = cursor.fetchall()

				creators = [{
					'id': row[0], 
					'email': row[1],
					'data': json.loads(row[2]),
					'created_at': row[6]
				} for row in rows]
			else:
				# Fallback to the original logic if no selected_creators

				if multiple:
					cursor.execute(
						"SELECT COUNT(*) FROM creators WHERE admin = ? AND category = ?",
						(admin, category)
					)
					total_creators = cursor.fetchone()[0]

					cursor.execute(
						"SELECT * FROM creators WHERE admin = ? AND category = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
						(admin, category, limit, offset)
					)
					rows = cursor.fetchall()

					creators = [{
						'id': row[0], 
						'email': row[1],
						'data': json.loads(row[2]),
						'created_at': row[6]
					} for row in rows]
				else:
					cursor.execute("SELECT * FROM creators WHERE id = ?", (creator,))
					row = cursor.fetchone()

					creators = {
						'id': row[0], 
						'email': row[1],
						'data': json.loads(row[2]),
						'created_at': row[6]
					} if row is not None else {}

			success = True
		except Exception as error:
			success, creators = False, f'Error getting creators: {error}'
		finally:
			conn.close()
			return success, creators, total_creators


		
	@staticmethod
	def check_creator(creator_email,admin):
		print(creator_email,admin)
		success, creator = False,{}
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute("SELECT * FROM creators WHERE email = ? AND admin = ?", (creator_email,admin))
			row = cursor.fetchone()
			creator = {
				'id': row[0], 
				'email':row[1],
				'data': json.loads(row[2]),
				'created_at': row[6]
			} if row is not None else {}
			success = True

		except Exception as error:
			success, creator = False, f'Error getting creators:{error}'

		finally:
			conn.close()
			return success, creator

	@staticmethod
	def add_task(task_id, task:dict):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:

			admin = task['admin']
			status = task['status']
			action_count = task['action_count']
			message = task['message']
			task_type = task['type']
			config = json.dumps(task.get('config',{}))

			
			cursor.execute("INSERT INTO tasks (id, status, admin, action_count, message, type, config) VALUES (?, ?, ?, ?, ?, ?, ?)", 
						   (task_id, status, admin, action_count, message, task_type, config))
			conn.commit()
			
			success,msg = True, 'Task added successfully'
		except Exception as error:
			success,msg =  False, str(error)
		finally:
			conn.close()
			return success,msg

	@staticmethod
	def delete_task(task_id):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:

			cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
			conn.commit()

			success,msg =  True,'Task deleted successfully'

		except Exception as error:
			success,msg = False,str(error)
		finally:
			conn.close()
			return success,msg

	@staticmethod
	def update_task(task_id, task):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			current_day = task.get('current_day',None)
			if current_day:
				cursor.execute("UPDATE tasks SET current_day = ? WHERE id = ?", (current_day,task_id))
			else:
				status,message = task['status'], task['message']
				cursor.execute("UPDATE tasks SET status = ?, message = ? WHERE id = ?", (status,message,task_id))
			
			conn.commit()
			
			success,msg = True, 'Task updated successfully'
		except Exception as error:
			success,msg = False, f'Error updating task {task_id} : {error}'
		finally:
			conn.close() 
			return success,msg   

	@staticmethod
	def get_task(task_id):
		success,msg = False,''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:

			cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
			row = cursor.fetchall()

			if not row:raise Exception(f'Task {task_id} not found')

			row = row[0]

			if row:success,msg = True,{
				'id': row[0], 
				'status': row[1], 
				'admin':row[2],
				'action_count':row[3],
				'message':row[4],
				'type':row[5],
				'config':row[6],
				'created_at': row[8]}

			else:success,msg = False,f'Could not get task {task_id}'

		except Exception as error:
			success,msg = False, str(error)
		
		finally:
			conn.close()
			return success,msg 

	@staticmethod
	def check_task_status(task_id):
		return Utils.get_task(task_id)
	
	@staticmethod
	def get_tasks(admin='', limit=20, offset=0, constraint=None, keyword=None):
		success, tasks, total_tasks = True, [], 0
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			if constraint is not None and keyword is not None:
				cursor.execute(f"SELECT COUNT(*) FROM tasks WHERE {constraint} = ? AND admin = ?", (keyword, admin))
				total_tasks = cursor.fetchone()[0]

				cursor.execute(
					f"""SELECT * FROM tasks 
					WHERE {constraint} = ? AND admin = ?
					ORDER BY created_at DESC 
					LIMIT ? OFFSET ?""",
					(keyword, admin, limit, offset)
				)
				rows = cursor.fetchall()

				tasks = [{
					'id': row[0], 
					'status': row[1], 
					'admin':row[2],
					'action_count':row[3],
					'message':row[4],
					'type':row[5],
					'config':row[6],
					'created_at': row[8]
				} for row in rows]
			else:

				cursor.execute("SELECT COUNT(*) FROM tasks WHERE admin = ?", (admin,))
				total_tasks = cursor.fetchone()[0]

				cursor.execute("SELECT * FROM tasks WHERE admin = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (admin, limit, offset))
				rows = cursor.fetchall()

				tasks = [{
					'id': row[0], 
					'status': row[1], 
					'admin':row[2],
					'action_count':row[3],
					'message':row[4],
					'type':row[5],
					'config':row[6],
					'created_at': row[8]} for row in rows]

		except Exception as error:
			success, tasks = False, str(error)

		finally:
			conn.close()
			return success, tasks, total_tasks
		
	
	@staticmethod
	def add_message(message_id, admin, creator_id, creator_name, recipient_id, recipient_name, has_media, link, sender_status, caption, price):
		success, msg = False, ''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute("""INSERT INTO messages \
				(id, admin, creator_id, creator_name, recipient_id, recipient_name, has_media, link, sender_status, caption, price) \
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
				(message_id, admin, creator_id, creator_name, recipient_id, recipient_name, has_media, link, sender_status, caption, price))
			conn.commit()
			success, msg = True, 'Message added successfully'
		except Exception as error:
			success, msg = False, str(f'Error adding message :{error}')
		finally:
			conn.close()
			return success, msg

	@staticmethod
	def delete_message(message_id):
		success, msg = False, ''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute('DELETE FROM messages WHERE id = ?', (message_id,))
			conn.commit()
			success, msg = True, 'Message deleted successfully'
		except Exception as error:
			success, msg = False, str(error)
		finally:
			conn.close()
			return success, msg

	@staticmethod
	def update_message(message_id, admin, creator_id, creator_name, recipient_id, recipient_name, has_media, link, sender_status, caption, price):
		success, msg = False, ''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute('''UPDATE messages SET
				admin = ?,
				creator_id = ?,
				creator_name = ?,
				recipient_id = ?,
				recipient_name = ?,
				has_media = ?,
				link = ?,
				sender_status = ?,
				caption = ?,
				price = ?
				WHERE id = ?''',
				(admin, creator_id, creator_name, recipient_id, recipient_name, has_media, link, sender_status, caption, price, message_id))
			conn.commit()
			success, msg = True, 'Message updated successfully'
		except Exception as error:
			success, msg = False, f'Error updating message: {error}'
		finally:
			conn.close()
			return success, msg
		

	@staticmethod
	def get_messages(limit=20, offset=0, admin=None, multiple=True, message_id=None, keyword=None, constraint=None):
		success, messages, total_messages = True, [], 0
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			if multiple:
				if constraint is not None and keyword is not None:
					cursor.execute(f"SELECT COUNT(*) FROM messages WHERE {constraint} = ? AND admin = ?", (keyword, admin))
					total_messages = cursor.fetchone()[0]
					cursor.execute(
						f"""SELECT * FROM messages \
						WHERE {constraint} = ? AND admin = ?
						ORDER BY created_at DESC \
					LIMIT ? OFFSET ?""", (keyword, admin, limit, offset))
				else:
					cursor.execute("SELECT COUNT(*) FROM messages WHERE admin = ?", (admin,))
					total_messages = cursor.fetchone()[0]
					cursor.execute("SELECT * FROM messages WHERE admin = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (admin, limit, offset))

				rows = cursor.fetchall()
				messages = [{
					'id': row[0],
					'admin': row[1],
					'creator_id': row[2],
					'creator_name': row[3],
					'recipient_id': row[4],
					'recipient_name': row[5],
					'has_media': row[6],
					'link': row[7],
					'sender_status': row[8],
					'caption': row[9],
					'price': row[10],
					'created_at': row[11]
				} for row in rows]
			else:
				cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
				row = cursor.fetchone()
				if row is None:
					raise Exception('message not found')
				messages = {
					'id': row[0],
					'admin': row[1],
					'creator_id': row[2],
					'creator_name': row[3],
					'recipient_id': row[4],
					'recipient_name': row[5],
					'has_media': row[6],
					'link': row[7],
					'sender_status': row[8],
					'caption': row[9],
					'price': row[10],
					'created_at': row[11]
				}
		except Exception as error:
			success, messages = False, f'Error getting messages:{error}'
		finally:
			conn.close()
			return success, messages, total_messages


	@staticmethod
	def time_diff(timestamp):
		try:
			time_secs = timestamp / 1000
			datetime_object = datetime.utcfromtimestamp(time_secs)
			current_time = datetime.utcnow()
			return True,current_time > datetime_object

		except Exception as error:
			return False,error


	@staticmethod
	def write_log(message, log_file_path=logs_file):
		current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

		with open(log_file_path, 'a',encoding='utf-8') as log_file:
			log_file.write(f"[{current_datetime}] [LOG] {message}\n")

		print(message)
		
	@staticmethod
	def update_client(client_msg):
		try:
			import sys
			configs = sys.modules.get('app_configs') or sys.modules.get('bot.app_configs')
			if configs is None:
				try:
					import app_configs as configs
				except ImportError:
					import bot.app_configs as configs
			with configs.app.app_context():
				configs.socketio.emit(
					'update-client',
					client_msg,
					namespace='/',
				)
			return True, 'client updated'
		except Exception as error:
			return False,error

	@staticmethod
	def push_task_update(task, status, message, client_status=None):
		if client_status is None:
			client_status = 'error' if status in ['failed', 'canceled', 'cancelled'] else 'success'
		success, msg = Utils.update_task(task['id'], {'status': status, 'message': message})
		if not success:
			Utils.write_log(msg)
		task.update({
			'status': status,
			'message': message,
			'updated': str(datetime.now())
		})
		success, msg = Utils.update_client({'msg': message, 'status': client_status, 'type': 'message'})
		if not success:
			Utils.write_log(msg)
		success, msg = Utils.update_client({'task': task, 'type': 'task'})
		if not success:
			Utils.write_log(msg)
		return True, message

	@staticmethod
	def add_user(user_id, admin, username, commented_at=None, task_id=None):
		success, msg = False, ''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute(
				"INSERT INTO users (id, admin, username, commented_at, task_id) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
				(user_id, admin, username, commented_at, task_id)
			)
			conn.commit()
			success, msg = True, 'User added successfully'
		except Exception as error:
			success, msg = False, str(f'Error adding user :{error}')
		finally:
			conn.close()
			return success, msg

	@staticmethod
	def update_user(user_id, status):
		success, msg = False, ''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
			conn.commit()
			success, msg = True, 'User updated successfully'
		except Exception as error:
			success, msg = False, str(f'Error updating user :{error}')
		finally:
			conn.close()
			return success, msg

	@staticmethod
	def delete_user(user_id):
		success, msg = False, ''
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
			conn.commit()
			success, msg = True, 'User deleted successfully'
		except Exception as error:
			success, msg = False, str(error)
		finally:
			conn.close()
			return success, msg

	@staticmethod
	def get_users(admin, limit=20, offset=0, constraint=None, keyword=None, category=None):
		success, users, total_users = True, [], 0
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			if constraint is not None and keyword is not None:
				cursor.execute(f"SELECT COUNT(*) FROM users WHERE {constraint} = ? AND admin = ?", (keyword, admin))
				total_users = cursor.fetchone()[0]
				cursor.execute(
					f"""SELECT * FROM users
					WHERE {constraint} = ? AND admin = ?
					ORDER BY created_at DESC
					LIMIT ? OFFSET ?""",
					(keyword, admin, limit, offset)
				)
				rows = cursor.fetchall()
			elif category in ['scraped', 'uploaded']:
				cursor.execute("SELECT COUNT(*) FROM users WHERE admin = ? AND category = ?", (admin, category))
				total_users = cursor.fetchone()[0]
				cursor.execute(
					"SELECT * FROM users WHERE admin = ? AND category = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
					(admin, category, limit, offset)
				)
				rows = cursor.fetchall()
			else:
				cursor.execute("SELECT COUNT(*) FROM users WHERE admin = ?", (admin,))
				total_users = cursor.fetchone()[0]
				cursor.execute(
					"SELECT * FROM users WHERE admin = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
					(admin, limit, offset)
				)
				rows = cursor.fetchall()

			users = [{
				'id': row[0],
				'admin': row[1],
				'username': row[2],
				'commented_at': row[3],
				'task_id': row[4],
				'created_at': row[5],
				'status': row[6],
				'spent': row[7],
				'category': row[8]
			} for row in rows]
		except Exception as error:
			success, users = False, str(error)
		finally:
			conn.close()
			return success, users, total_users

	@staticmethod
	def get_existing_user_ids(user_ids, admin=None):
		success, existing = True, []
		conn = sqlite3.connect(db_file)
		cursor = conn.cursor()
		try:
			if not user_ids:
				return True, []
			placeholders = ",".join(["?"] * len(user_ids))
			params = list(user_ids)
			if admin is not None:
				query = f"SELECT id FROM users WHERE id IN ({placeholders}) AND admin = ?"
				params.append(admin)
			else:
				query = f"SELECT id FROM users WHERE id IN ({placeholders})"
			cursor.execute(query, params)
			existing = [row[0] for row in cursor.fetchall()]
		except Exception as error:
			success, existing = False, str(error)
		finally:
			conn.close()
			return success, existing

	@staticmethod
	def get_unmessaged_users(creator_id, limit=50, offset=0):
		try:
			conn = sqlite3.connect(db_file)
			cursor = conn.cursor()
			cursor.execute("""
				SELECT u.id, u.username
				FROM users u
				WHERE u.status = 'active'
				AND u.id NOT IN (
					SELECT m.recipient_id
					FROM messages m
					WHERE m.creator_id = ?
				)
				ORDER BY u.created_at DESC
				LIMIT ? OFFSET ?
			""", (creator_id, limit, offset))
			rows = cursor.fetchall()
			conn.close()
			users = [{'id': row[0], 'username': row[1]} for row in rows]
			return True, users
		except Exception as e:
			return False, str(e)

	@staticmethod
	def add_users(users, admin=None, task_id=None):
		try:
			for user in users:
				success, msg = Utils.add_user(
					user.get('_id') or user.get('id'),
					admin,
					user.get('username') or user.get('name'),
					user.get('commented_at'),
					task_id=task_id
				)
				if not success:
					return False, msg
			return True, f'saved all {len(users)} users'
		except Exception as error:
			return False, error

	@staticmethod
	def check_values(values:list):
		for value in values:
			if value is None or not value or len(value) < 1:
				return False
		else:return True



