# -*- coding: utf-8 -*-

from app_configs import creators_file,configs_folder,universal_files
from utils import Utils
from configs import *
import io


class Creator:
	def __init__(self):
		self.proxies = Utils.load_proxies()
		self.headers = {
			'authority': 'rest.4based.com',
			'accept': 'application/json',
			'accept-language': 'en-US,en;q=0.9',
			'content-type': 'application/json',
			'origin': 'https://4based.com',
			'referer': 'https://4based.com/',
			'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
			'sec-ch-ua-mobile': '?1',
			'sec-ch-ua-platform': '"Android"',
			'sec-fetch-dest': 'empty',
			'sec-fetch-mode': 'cors',
			'sec-fetch-site': 'same-site'
		}

	def generate_sensor_data(self, type='x-auth-resource'):
		if type == 'x-auth-resource':
			return ''.join(random.choices(string.ascii_letters.upper() + string.digits + string.ascii_letters, k=len('2NTN5vEez9')))

	def update_media_id(self, post_id, creator):
		try:
			creator_id = creator.get('id', None)

			# Get post ID
			self.headers.update({
				'user-agent': Utils.generate_user_agent('android',1),
			})

			params = {
				'with_first_three_comments': 'true',
				'with_source': 'true',
			}

			response = requests.get(
				f'https://rest.4based.com/api/1.0/file-stack/{post_id}',
				params=params,
				headers=self.headers,
				proxies=random.choice(self.proxies),
				timeout=60
			)
			if not response.ok:
				raise Exception(f'Error fetching media ID for {post_id}: {response.text}')

			media_id = response.json().get('vault_file_stack_id', None)
			if not media_id or media_id is None:
				raise Exception(f'No media ID found for {post_id}')
			
			success, msg = self.update(creator, {'media_id': media_id,'post_id':post_id})
			if not success:
				raise Exception(f'Error updating creator {creator_id} with media ID {media_id}: {msg}')

			return True, f'Successfully saved media ID {media_id} for creator {creator_id}'
		except Exception as e:
			return False, f'Error saving media ID {media_id} for creator {creator_id}: {str(e)}'

	def upload_media(self, session, creator_id, media_id, creator_name, user_name,  caption, is_paid=False, price=0):
		try:
			# Send media data
			json_data = {
				'vaults_to_file_stack': {
					'vaults': [
						{
							'id': f'{media_id}',
							'guid': str(uuid.uuid4()),
							'position': 0,
						},
					],
					'description': caption,
					'price': 0 if not is_paid else price,
					'status': 'available',
					'is_subscription_item': is_paid,
					'additional_categories': [
						'chat_message',
					],
					'guid': str(uuid.uuid4()),
				},
			}

			response = session.post(
				f'https://rest.4based.com/api/1.0/user/{creator_id}/file-stack/',
				json=json_data,
				timeout=60
			)

			if not response.ok:
				raise Exception(f'Error sending media data to {user_name} by {creator_name}: {response.text}')
			if not response.json().get('complete', False):
				raise Exception(f'Error sending media data to {user_name} by {creator_name}: {response.text}')
			media_id = response.json().get('_id')

			return True, media_id
		except Exception as e:
			return False, f'Error saving media for creator {creator_id}: {str(e)}'

	def scrape_users(self,scraper, admin,creator_id,count=40,offset=0):
		try:
			Utils.write_log(f'Scraping users for creator {creator_id}...')
			client_msg = {'msg':f'Scraping users for creator {creator_id}','status':'success','type':'message'}
			success,msg = Utils.update_client(client_msg)

			session = requests.Session()
			session.headers.update(scraper.get('headers'))
			session.cookies.update(scraper.get('cookies'))
			session.proxies = scraper.get('proxies',random.choice(self.proxies)) if scraper.get('reuse_ip',True) else random.choice(self.proxies)
			
			valid_users = []
			random_letter = random.choice(string.ascii_letters)

			params = {
				'offset': f'{offset}',
				'limit': f'{count}',
				'search': random_letter,
				'sort': '{"follower_count":"asc"}',
				# 'verified': 'true',
				'role': 'client',
			}

			response = session.get(
				'https://rest.4based.com/api/1.0/user', 
				params=params,
				timeout=60)

			if response.ok:
				users = response.json()
				if isinstance(users, list) and len(users) >= count//2:
					success, messages, total_messages = Utils.get_messages(
					admin=admin,
					limit=100, 
					offset=0,
					constraint='creator_id',
					keyword=creator_id)
					
					if not success:return False, messages
					
					len_messages = len(messages)
					
					if len_messages < total_messages:
						for i in range(total_messages - len_messages):
							offset = len_messages + i
							
							success, msg, total_messages = Utils.get_messages(
								admin=admin,
								limit=100,
								offset=offset,
								constraint='creator_id',
								keyword=creator_id)
							
							if not success: return False, msg

							messages += msg

					for user in users:
						if not user.get('creator', False) and user.get('cold_communication_status') != 'actively_not_contactable':
							# Check if user has already been messaged as a recipient
							user_messages = [msg for msg in messages if msg.get('recipient_id', None) == user.get('_id', None)]
							if not user_messages:
								valid_users.append(user)
					
					Utils.write_log(f'Found {len(valid_users)} valid users for creator {creator_id}')
					client_msg = {'msg':f'Found {len(valid_users)} valid users for creator {creator_id}','status':'success','type':'message'}
					success,msg = Utils.update_client(client_msg)
					# with open ('users.json','w',encoding='utf-8') as f:
					# 	json.dump(valid_users,f,ensure_ascii=False,indent=4)
					return True, valid_users
				else:
					return False, 'No valid users found'
			else:
				return False, f'Error fetching users: {response.text}'

		except Exception as e:
			return False, f'Error scraping users: {str(e)}'

	def send_messages(self,admin,task_id,creator,scrapers,config,maxworkers):
		try:
			
			success,task_status = Utils.check_task_status(task_id)
			if not success:raise Exception(task_status)
			if task_status['status'].lower() in ['cancelled','canceled']:return False,  'Task canceled'

			creator_data = creator['data']
			creator_name = creator_data['details']['user']['name']
			email = creator_data['details']['user']['identifier']
			password = creator_data['details']['user']['password']
			creator_id = creator_data['details']['user']['_id']
			creator_internal_id = creator['id']

			caption = config.get('caption','')
			caption_source = config.get('caption_source','creator')
			has_media = config.get('has_media',False)
			media_id = creator_data.get('media_id',None)
			Utils.write_log(f'=== {config} ===')

			is_paid = False if config.get('cost_type','free') == 'free' else True
			price = config.get('price',0)

			success,_creator = self.login(
				admin,
				email,
				password,
				reuse_ip=creator.get('reuse_ip',True),
				task_id=task_id
			)
			if not success:raise Exception(_creator)

			creator = _creator

			if len(scrapers) < 1:raise ValueError('You must supply accounts for scraping')
			target_scraper = random.choice(scrapers)
			success,scraper = self.login(
				admin,
				target_scraper['email'],
				target_scraper['data']['details']['user']['password'],
				reuse_ip=target_scraper.get('reuse_ip',True),
				task_id=task_id
			)
			if not success:raise Exception(scraper)

			if caption_source == 'creator':
				captions_file = os.path.join(configs_folder,creator_internal_id,'captions.txt')
				if not isfile(captions_file):raise Exception(f'Captions file does not exist for {creator_name}')
				
				with open(captions_file,'r',encoding='utf-8') as f:
					captions = [line.strip() for line in f.readlines()]
					if len(captions) < 1: raise ValueError('Captions can not be empty')
					caption = random.choice(captions)

			if (not 'headers' in creator.keys() or len(creator.get('headers',{})) < 1) or (not 'cookies' in creator.keys() or len(creator.get('cookies',{})) < 1):
				return False,f'User {creator_name} does not have session data'
			
			session = requests.Session()
			session.headers.update(creator.get('headers'))
			session.cookies.update(creator.get('cookies'))
			session.proxies = creator.get('proxies',random.choice(self.proxies)) if creator.get('reuse_ip',True) else random.choice(self.proxies)

			users,found_users = [],0
			while found_users < 1:
				success, users = self.scrape_users(scraper, admin, creator_id, count=maxworkers, offset=random.randint(0, 300))
				if not success:raise Exception(users)
				found_users += len(users)

			for user in users:
				# create a chat ID for the user
				response = session.post(
				    f'https://rest.4based.com/api/1.0/user/{creator_id}/chat/user/{user["_id"]}',
					timeout=60
				)

				if not response.ok and response.status_code != 409:
					client_msg = {'msg':f'Error sending message to {user["name"]}: by  {creator_name} {response.text}','status':'error','type':'message'}
					success,msg = Utils.update_client(client_msg)

					raise Exception(f'Error sending message to {user["name"]}: by  {creator_name} {response.text}')
				
				elif response.status_code == 409:
					Utils.write_log(f'User {user["name"]} already has a chat with {creator_name}, skipping...')
					continue
				
				message_id = response.json().get('_id', None)
				message_key = response.json().get('user_key', None)

				# If media is to be sent, save it and get the media ID
				if has_media and media_id:
					success, media_id = self.upload_media(
						session,
						creator_id,
						media_id,
						creator_name,
						user['name'],
						caption,
						is_paid=is_paid,
						price=price
					)
					if not success:raise Exception(media_id)

				# Send message to the user
				json_data = {
					'message': caption,
					'sender_status': 'sent',
					'local_id': str(uuid.uuid4()),
				}
				if has_media and media_id:json_data['file_stack_id'] = media_id

				response = session.post(
					f'https://rest.4based.com/api/1.0/user/{creator_id}/chat/{message_id}/message',
					json=json_data,
					timeout=60
				)

				if not response.ok:
					raise Exception(f'Error sending message to {user["name"]} by {creator_name}: {response.text}')
				
				success, msg = Utils.add_message(
					message_id,
					admin,
					creator_internal_id,
					creator_name,
					user['_id'],
					user['name'],
					has_media,
					f'https://4based.com/chat/{message_id}/conversation',
					json_data['sender_status'],
					caption,
					price
				)

				if not success:
					raise Exception(f'Error adding message to database for {user["name"]} by {creator_name}: {msg}')

				Utils.write_log(f'=== Successfully sent a message to {user["name"]} by {creator_name} ===')
				client_msg = {'msg':f'Successfully sent a message to {user["name"]} by {creator_name}','status':'success','type':'message'}
				success,msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)

				time.sleep(random.randint(5, 10))  # Sleep to avoid rate limiting

			Utils.write_log(f'=== Successfully sent messages to {len(users)} users by {creator_name} ===')
			client_msg = {'msg':f'Successfully sent messages to {len(users)} users by {creator_name}','status':'success','type':'message'}
			success,msg = Utils.update_client(client_msg)
			if not success:Utils.write_log(msg)

			return True, f'Successfully sent messages to {len(users)} users by {creator_name}'
		
		except ValueError as ve:
			return False, f'Value error while sending messages to users for {creator.get("id")}: {str(ve)}'
		
		except Exception as e:
			return False,f'Error sending messages to users for {creator.get("id")}: {str(e)}'

	def login(self,admin,email,password,reuse_ip=True,task_id=None,category='creators'):
		try:
			success,result,user,new_user = False,'Login failed',{},True
			
			if task_id is not None:
				success,task_status = Utils.check_task_status(task_id)
				if not success:raise Exception(task_status)
				if task_status['status'].lower() in ['cancelled','canceled']:return False,  'Task canceled'
				

			success,user = Utils.check_creator(email,admin)
			if not success:raise Exception(user)

			creator_id = user.get('id',None)
			user = user.get('data',{})

			if len(user.items()) >= 1 and (user.get('cookies',False) and user.get('headers',False)):new_user = False

			if reuse_ip and 'proxies' in user.keys():
				proxies = user['proxies']
			else: proxies = random.choice(self.proxies)

			# check session
			if not new_user:
				params = {
					'with_user_pivot_interaction': 'true',
				}

				response = requests.get(
					f"https://rest.4based.com/api/1.0/user/name/{user['details']['user']['name']}",
					params=params,
					headers=user.get('headers',{}),
					cookies=user.get('cookies',{}),
					proxies=proxies,
					timeout=60
				)

				if response.status_code == 200:
					user['id'] = creator_id
					user['status'] = 'Online'
					return True, user
				
			json_data = {
				'identifier': email,
				'password': password,
				'locale': 'en',
			}

			self.headers.update({
				'user-agent': Utils.generate_user_agent('android',1),
				'x-auth-resource': self.generate_sensor_data(),
			})
			
			session = requests.Session()
			session.headers.update(self.headers)
			session.proxies = proxies

			response = session.post(
				'https://rest.4based.com/api/1.0/auth/login',  
				json=json_data,
				timeout=60
			)

			if response.status_code == 400:
				if 'password not correct' in response.json().values():
					success,result = True,'password not correct'
			
			elif not response.ok:
				user['status'] = 'Offline'
				success,result = False,response.text

			else:
				data = response.json()
				token,auth_resource = data['credentials']['token'],data['credentials']['resource']

				user['status'] = 'Online'
				user['details'] = data
				user['details']['user']['password'] = password
				avatar = user['details']['user']['avatar']
				
				session.headers.update({
					'x-auth-resource': auth_resource,
					'x-auth-token':token
				})

				user['headers'] = dict(session.headers)
				user['cookies'] = session.cookies.get_dict()

				if avatar is not None:
					image_url = f'https://pic.4based.com/preview/{avatar["code"]}/{avatar["_id"]}/300x300.{avatar["extension"]}'
					user['details']['user']['picture'] = image_url

				user['proxies'] = proxies
				user['reuse_ip'] = reuse_ip

			if new_user:
				creator_id = str(uuid.uuid4()).upper()[:8]
				success,msg = Utils.add_creator(creator_id,email,user,admin,category=category,task_id=task_id)

				images_folder = os.path.join(configs_folder,creator_id,'images')
				videos_folder = os.path.join(configs_folder,creator_id,'videos')
				captions_file = os.path.join(configs_folder,creator_id,'captions.txt')
				
				os.makedirs(images_folder,exist_ok=True)
				os.makedirs(videos_folder,exist_ok=True)
				with open(captions_file, 'w') as file:file.write("")

			else:
				success,msg = Utils.update_creator(creator_id,email,user)
			if not success:raise Exception(msg)

			user['id'] = creator_id
			success,result = True,user
			return success,result
		
		except Exception as error:
			error = f'Error logging in {error} on {email}'
			Utils.write_log(error)
			return False,error
			
	def update(self,user:dict,data:dict):
		try:
			user_email,user_id,user = user['email'],user['id'],user['data']
			for key,value in data.items():
				user[key] = value
			success,msg = Utils.update_creator(user_id,user_email,user)
			if not success:raise Exception(msg)
			return True,user
		except Exception as error:
			return False, error
	

class _4BASED:

	def __init__(self):
		self.proxies = Utils.load_proxies()
		self.headers = {
			'authority': 'rest.4based.com',
			'accept': 'application/json',
			'accept-language': 'en-US,en;q=0.9',
			'content-type': 'application/json',
			'origin': 'https://4based.com',
			'referer': 'https://4based.com/',
			'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
			'sec-ch-ua-mobile': '?1',
			'sec-ch-ua-platform': '"Android"',
			'sec-fetch-dest': 'empty',
			'sec-fetch-mode': 'cors',
			'sec-fetch-site': 'same-site'
		}


	def add_creators(self,admin,task,creators,category):
		task_status,task_msg,completed,fails = 'failed',f'Started logging in creators for {task["id"]}',0,0
		try:
			Utils.write_log(f'=== Add {category} started for {task["id"]} ===')
			task_id = task['id']

			with ThreadPoolExecutor(max_workers=10) as executor:
				args = [(
					admin,
					creator['email'],
					creator['password']
					) for creator in creators]
				kwargs = [{'task_id':task_id,'category':category} for creator in creators]
				
				futures = []
				for arg,kwarg in zip(args,kwargs):
					success,task_status = Utils.check_task_status(task_id)
					if not success:raise Exception(task_status)
					if task_status['status'].lower() in ['cancelled','canceled']:break

					future = executor.submit(Creator().login, *arg, **kwarg)
					futures.append(future)

				for future in as_completed(futures):
					success,task_status = Utils.check_task_status(task_id)
					if not success:raise Exception(task_status)
					
					if task_status['status'].lower() in ['cancelled','canceled']:
						for remaining_future in futures:
							remaining_future.cancel()
						break

					success,result = future.result()
					if success:
						completed += 1

						client_msg = {'msg':f'{completed} {category} added so far on task:{task_id}','status':'success','type':'message'}
						success,msg = Utils.update_client(client_msg)
						if not success:Utils.write_log(msg)


					elif not success and result == 'Task canceled':
						task_status = 'canceled'
						client_msg = {'msg':f'{result} task:{task_id}','status':'error','type':'message'}
						
						success,msg = Utils.update_client(client_msg)
						if not success:Utils.write_log(msg)
						break
					
					else:
						fails += 1
						client_msg = {'msg':f'{fails} creators added so far on task:{task_id}','status':'error','type':'message'}
						
						success,msg = Utils.update_client(client_msg)
						if not success:Utils.write_log(msg)
						task_msg = result

		except Exception as error:
			Utils.write_log(error)
			task_status = 'failed'
			task_msg = f'Error adding creators on {task_id} : {error}'

		finally:
			if task_status == 'canceled':
				client_msg = {'msg':f'{task_id} was canceled','status':'error','type':'message'}
				task_msg = client_msg['msg']

			elif completed == len(creators) and len(creators) > 0:
				task_status = 'success'
				client_msg = {'msg':f'{task_id} successful','status':'success','type':'message'}
				
			elif  fails > len(creators) // 2:
				client_msg = {'msg':f'{task_id} failed','status':'error','type':'message'}
				task_status = 'failed'
				task_msg = client_msg['msg']
			
			elif task_status == 'failed':
				client_msg = {'msg':f'{task_id} failed','status':'error','type':'message'}
			
			else:
				task_status = 'completed'
				client_msg = {'msg':f'{completed} items successful task:{task_id}','status':'success','type':'message'}
				task_msg = client_msg['msg']

			success,msg = Utils.update_client(client_msg)
			if not success:Utils.write_log(msg)

			success,msg = Utils.update_task(task_id,{
				'status':task_status,
				'message':task_msg
			})
			if not success:Utils.write_log(msg)
			
			task_data = task
			task_data.update(
				{'updated':str(datetime.now()),
				'status':task_status})

			success,msg = Utils.update_client({'task':task_data,'type':'task'})
			if not success:Utils.write_log(msg)


	def start_messaging(self,task,maxworkers=10):
		task_status,task_msg = 'failed',f'Started messaging for {task["id"]}'
		try:

			admin = task['admin']
			task_id = task['id']
			config = task['config']
			selected_creators = config.get('select-creators',[])
			
			time_between = config.get('time_between')
			time_message = {
				'60':'1 minute',
				'120':'2 minutes',
				'180':'3 minutes',
				'300':'5 minutes',
				'600':'10 minutes',
				'1200':'20 minutes',
				'1800':'30 minutes',
				'3600':'1 hour',
				'7200':'2 hours',
				'10800':'3 hours',
				'21600':'6 hours',
				'86400':'24 hours'
			}

			success,creators,total_creators = Utils.get_creators(admin=admin,limit=100,selected_creators=selected_creators)
			if not success:raise Exception(creators)
			
			len_creators = len(creators)
			if len_creators < total_creators:
				for i in range(total_creators - len_creators):
					offset = len_creators + i
					success,msg,total_creators = Utils.get_creators(admin=admin,limit=100,offset=offset,selected_creators=selected_creators)
					if not success:raise Exception(msg)
					creators += msg


			success,scrapers,total_scrapers = Utils.get_creators(admin=admin,limit=100,category='users')
			if not success:raise Exception(scrapers)
			
			len_scrapers = len(scrapers)
			if len_scrapers < total_scrapers:
				for i in range(total_scrapers - len_scrapers):
					offset = len_scrapers + i
					success,msg,total_scrapers = Utils.get_creators(admin=admin,limit=100,offset=offset,category='users')
					if not success:raise Exception(msg)
					creators += msg

			Utils.write_log(f'=== Messaging started for {task_id} ===')

			while True:
				success,task_status = Utils.check_task_status(task_id)
				if not success:raise Exception(task_status)
				if task_status['status'].lower() in ['cancelled','canceled']:break

				with ThreadPoolExecutor(max_workers=maxworkers) as executor:
					args = [(
						admin,
						task_id,
						creator,
						scrapers,
						config,
						maxworkers
						) for creator in creators]
					
					futures = []
					for arg in args:
						success,task_status = Utils.check_task_status(task_id)
						if not success:raise Exception(task_status)
						if task_status['status'].lower() in ['cancelled','canceled']:break

						future = executor.submit(Creator().send_messages, *arg)
						futures.append(future)

					for future in as_completed(futures):
						success,task_status = Utils.check_task_status(task_id)
						if not success:raise Exception(task_status)
						
						if task_status['status'].lower() in ['cancelled','canceled']:
							for remaining_future in futures:
								remaining_future.cancel()
							break

						success,result = future.result()
						if not success:
							client_msg = {'msg':f'Error messaging creators on {task_id} : {result}','status':'error','type':'message'}

							success,msg = Utils.update_client(client_msg)
							if not success:Utils.write_log(msg)

						Utils.write_log(f'=== {result}===')

				wait_massage = f'Waiting for {time_message[str(time_between)]} before sending another batch of messages'
				Utils.write_log(wait_massage)
				# Update client with wait message
				client_msg = {'msg':wait_massage,'status':'success','type':'message'}
				success,msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)
				time.sleep(time_between)  # Sleep to avoid rate limiting

		except Exception as error:
			Utils.write_log(error)
			task_status = 'failed'
			task_msg = f'Error messaging creators on {task_id} : {error}'
			client_msg = {'msg':f'Error messaging creators on {task_id} : {error}','status':'error','type':'message'}

			success,msg = Utils.update_client(client_msg)
			if not success:Utils.write_log(msg)

			success,msg = Utils.update_task(task_id,{
				'status':task_status,
				'message':task_msg
			})

			task_data = task
			task_data.update(
				{'updated':str(datetime.now()),
				'status':task_status})

			success,msg = Utils.update_client({'task':task_data,'type':'task'})
			if not success:Utils.write_log(msg)
