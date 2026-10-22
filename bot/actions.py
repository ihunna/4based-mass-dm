# -*- coding: utf-8 -*-

from app_configs import creators_file,configs_folder,universal_files
from utils import Utils
from configs import *
import io, asyncio, traceback, threading
from urllib.parse import urlparse
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException


def _mitmweb_enabled():
	# Temporary mitmweb bypass: mitmproxy's cert fails normal TLS verify.
	# Set MITMWEB=0 in .env to restore certificate verification.
	return os.getenv('MITMWEB', '1').strip().lower() not in ('0', 'false', 'no')


def _tls_impersonate():
	# Python's ssl/httpx cannot reproduce mitmproxy's ClientHello. curl_cffi can.
	return os.getenv('TLS_IMPERSONATE', 'chrome_android').strip() or 'chrome_android'


def _cookies_as_dict(cookies):
	if not cookies:
		return {}
	if hasattr(cookies, 'jar'):
		return {cookie.name: cookie.value for cookie in cookies.jar}
	if hasattr(cookies, 'items'):
		return dict(cookies.items())
	return dict(cookies)


DEFAULT_TIMEOUT = 120


class NetworkError(Exception):
	"""Retryable transport failure (timeout, proxy, TLS, reset)."""


def _redact_proxy(proxy):
	if not isinstance(proxy, str) or not proxy:
		return proxy
	parsed = urlparse(proxy)
	if not parsed.password:
		return proxy
	host = parsed.hostname or ''
	port = f':{parsed.port}' if parsed.port else ''
	user = parsed.username or ''
	return f'{parsed.scheme}://{user}:***@{host}{port}'


def _timeout_seconds(timeout):
	if isinstance(timeout, tuple):
		return sum(float(part) for part in timeout)
	if timeout is None:
		return float(DEFAULT_TIMEOUT)
	return float(timeout)


def _format_network_error(error, method=None, url=None, proxy=None, timeout=None):
	raw = str(error).splitlines()[0].strip()
	lower = raw.lower()
	if 'curl: (28)' in raw or 'timed out' in lower:
		waited = f' after {_timeout_seconds(timeout):.0f}s' if timeout is not None else ''
		reason = f'Request timed out{waited}'
	elif 'curl: (7)' in raw:
		reason = 'Could not connect'
	elif 'curl: (56)' in raw:
		reason = 'Connection reset'
	elif 'curl: (35)' in raw:
		reason = 'TLS handshake failed'
	else:
		reason = raw[:180]

	details = []
	if method and url:
		details.append(f'{method} {url}')
	if proxy:
		details.append(f'proxy {_redact_proxy(proxy)}')
	if details:
		return f'{reason} ({", ".join(details)})'
	return reason


_DROP_RETRIES = 2
# the request never reached the server, so any method is safe to resend
_NOT_SENT_CODES = ('curl: (5)', 'curl: (7)', 'curl: (35)', 'curl: (60)', 'curl: (97)')
# the connection died mid-request; resend reads plus POST and PATCH
_DROPPED_CODES = ('curl: (16)', 'curl: (52)', 'curl: (55)', 'curl: (56)', 'curl: (92)')
_DROPPED_METHODS = ('GET', 'HEAD', 'POST', 'PATCH')


def _is_dropped_connection(error, method):
	raw = str(error)
	if any(code in raw for code in _NOT_SENT_CODES):
		return True
	return method.upper() in _DROPPED_METHODS and any(code in raw for code in _DROPPED_CODES)


async def _http_failure(response, action):
	body = (await response.text() or '').strip()
	snippet = ' '.join(body.split())[:240] or 'empty body'
	if snippet.lstrip().startswith('<') or 'Just a moment' in snippet or 'cf-mitigated' in response.headers:
		return f'{action} blocked by Cloudflare (HTTP {response.status})'
	return f'{action} failed (HTTP {response.status}): {snippet}'


def _is_retryable_error(result):
	if isinstance(result, NetworkError):
		return True
	if not isinstance(result, str):
		return False
	text = result.lower()
	return any(token in text for token in (
		'timed out', 'could not connect', 'network error', 'connection reset',
		'tls handshake', 'cloudflare', 'challenge', 'try another proxy',
		'curl: (28)', 'curl: (7)', 'curl: (56)', 'curl: (35)',
	))


class _Headers(dict):
	"""Case-insensitive headers, matching aiohttp's CIMultiDict usage."""

	@staticmethod
	def _norm(key):
		return str(key).lower()

	def __init__(self, data=None):
		super().__init__()
		if data:
			self.update(data)

	def update(self, other=None, **kwargs):
		if other:
			items = other.items() if hasattr(other, 'items') else other
			for key, value in items:
				self[key] = value
		for key, value in kwargs.items():
			self[key] = value
		return None

	def __setitem__(self, key, value):
		super().__setitem__(self._norm(key), value)

	def __getitem__(self, key):
		return super().__getitem__(self._norm(key))

	def __delitem__(self, key):
		super().__delitem__(self._norm(key))

	def __contains__(self, key):
		return super().__contains__(self._norm(key))

	def get(self, key, default=None):
		return super().get(self._norm(key), default)

	def pop(self, key, *args):
		return super().pop(self._norm(key), *args)


class _CookieJar:
	def __init__(self):
		self._pending = {}
		self._client = None

	def bind(self, client):
		self._client = client
		if self._pending:
			client.cookies.update(self._pending)
			self._pending.clear()

	def update_cookies(self, cookies):
		if not cookies:
			return
		data = dict(cookies)
		if self._client is not None:
			self._client.cookies.update(data)
		else:
			self._pending.update(data)

	def filter_cookies(self, url):
		matched = dict(self._pending)
		if self._client is None:
			return matched
		cookies = getattr(self._client, 'cookies', None)
		if hasattr(cookies, 'jar'):
			host = (urlparse(url).hostname or '').lower()
			for cookie in cookies.jar:
				domain = (cookie.domain or '').lstrip('.').lower()
				if not domain or not host or host == domain or host.endswith('.' + domain):
					matched[cookie.name] = cookie.value
			return matched
		matched.update(_cookies_as_dict(cookies))
		return matched

	def snapshot(self):
		cookies = dict(self._pending)
		if self._client is not None:
			cookies.update(_cookies_as_dict(getattr(self._client, 'cookies', None)))
		return cookies


_CURL_HTTP_VERSIONS = {
	1: 'HTTP/1.0',
	2: 'HTTP/1.1',
	3: 'HTTP/2',
	4: 'HTTP/2',
	5: 'HTTP/2',
	30: 'HTTP/3',
}


class _HttpResponse:
	def __init__(self, response):
		self._response = response
		self.status = response.status_code
		self.headers = response.headers
		version = getattr(response, 'http_version', None)
		self.http_version = _CURL_HTTP_VERSIONS.get(version, version)

	@property
	def ok(self):
		is_success = getattr(self._response, 'is_success', None)
		if is_success is not None:
			return is_success
		return 200 <= self.status < 300

	async def text(self):
		text = self._response.text
		if asyncio.iscoroutine(text):
			return await text
		return text

	async def json(self):
		data = self._response.json()
		if asyncio.iscoroutine(data):
			return await data
		return data

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, tb):
		close = getattr(self._response, 'aclose', None) or getattr(self._response, 'close', None)
		if not close:
			return
		result = close()
		if asyncio.iscoroutine(result):
			await result


class _RequestContext:
	def __init__(self, session, method, url, kwargs):
		self._session = session
		self._method = method
		self._url = url
		self._kwargs = kwargs
		self._response = None

	async def __aenter__(self):
		self._response = await self._session._request(self._method, self._url, **self._kwargs)
		return self._response

	async def __aexit__(self, exc_type, exc, tb):
		if self._response is not None:
			return await self._response.__aexit__(exc_type, exc, tb)


class _HttpSession:
	"""curl_cffi session with browser TLS/HTTP2 fingerprints, aiohttp-style API."""

	def __init__(self, headers=None):
		self.headers = _Headers(headers or {})
		self.cookie_jar = _CookieJar()
		self._client = None
		self._proxy = None
		self._lock = asyncio.Lock()

	async def __aenter__(self):
		return self

	async def __aexit__(self, exc_type, exc, tb):
		await self.close()

	@staticmethod
	def _normalize_proxy(proxy):
		if isinstance(proxy, dict):
			return proxy.get('http') or proxy.get('https')
		return proxy

	async def _ensure_client(self, proxy=None, reconnect=False):
		proxy = self._normalize_proxy(proxy)
		async with self._lock:
			if not reconnect and self._client is not None and (proxy is None or proxy == self._proxy):
				return
			cookies = self.cookie_jar.snapshot()
			if self._client is not None:
				await self._client.close()
			if proxy is not None:
				self._proxy = proxy
			self._client = AsyncSession(
				impersonate=_tls_impersonate(),
				proxy=self._proxy,
				verify=not _mitmweb_enabled(),
				allow_redirects=True,
				cookies=cookies,
				timeout=DEFAULT_TIMEOUT,
			)
			self.cookie_jar.bind(self._client)

	async def _request(self, method, url, **kwargs):
		proxy = kwargs.pop('proxy', None)
		timeout = kwargs.pop('timeout', DEFAULT_TIMEOUT)
		await self._ensure_client(proxy)
		used_proxy = proxy or self._proxy
		for attempt in range(1, _DROP_RETRIES + 2):
			try:
				response = await self._client.request(
					method,
					url,
					headers=dict(self.headers),
					timeout=timeout,
					**kwargs,
				)
				return _HttpResponse(response)
			except (RequestException, OSError) as error:
				if attempt > _DROP_RETRIES or not _is_dropped_connection(error, method):
					raise NetworkError(_format_network_error(
						error, method, url, used_proxy, timeout
					)) from error
				await asyncio.sleep(attempt)
				await self._ensure_client(proxy, reconnect=True)

	def get(self, url, **kwargs):
		return _RequestContext(self, 'GET', url, kwargs)

	def post(self, url, **kwargs):
		return _RequestContext(self, 'POST', url, kwargs)

	def patch(self, url, **kwargs):
		return _RequestContext(self, 'PATCH', url, kwargs)

	async def close(self):
		if self._client is not None:
			await self._client.close()
			self._client = None


def _http_session(**kwargs):
	return _HttpSession(headers=kwargs.get('headers'))


class Cancelled(Exception):
	"""Raised when a running task is cancelled."""
	pass


class SessionExpired(Exception):
	"""4based rejected the creator's session token (HTTP 401)."""


_session_locks = {}
_session_locks_guard = threading.Lock()


def _session_lock(key):
	# Tasks run in separate threads with their own event loops, so an asyncio.Lock can't be shared.
	with _session_locks_guard:
		return _session_locks.setdefault(key, threading.Lock())


def _auth_token(headers):
	return (headers or {}).get('x-auth-token') or (headers or {}).get('X-Auth-Token')


def _parse_utc(value):
	try:
		return datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
	except (TypeError, ValueError):
		return None


async def _response_json(response):
	text = await response.text()
	if text.lstrip().startswith('<') or 'Just a moment' in text or 'cf-mitigated' in response.headers:
		raise Exception(
			'This request was blocked with a Cloudflare challenge. '
			'The proxy or IP is being challenged; try another proxy.'
		)
	try:
		return json.loads(text)
	except json.JSONDecodeError as error:
		raise Exception(
			f'Invalid JSON ({response.status}): {text[:180]}'
		) from error


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

	def _reuse_ip(self, account, config=None):
		if config and config.get('proxy_flush'):
			return False
		data = account.get('data') or account
		return data.get('reuse_ip', account.get('reuse_ip', True))

	def apply_proxy_flush(self, accounts):
		updated = 0
		for account in accounts:
			data = account.get('data') or {}
			if not data.get('proxies'):
				continue
			success, msg = self.update(account, {'reuse_ip': False})
			if success:
				account['data']['reuse_ip'] = False
				updated += 1
			else:
				Utils.write_log(msg)
		return updated

	def _proxy(self, stored=None, reuse_ip=True):
		if reuse_ip and stored:
			return Utils.format_proxy(stored) if isinstance(stored, dict) else stored
		return Utils.format_proxy(random.choice(self.proxies))

	async def update_media_id(self, post_id, creator):
		media_id = None
		creator_id = creator.get('id', None)
		try:
			async with _http_session(headers=self.headers) as session:
				session.headers.update({
					'user-agent': Utils.generate_user_agent('android',1),
				})
				params = {
					'with_first_three_comments': 'true',
					'with_source': 'true',
				}
				proxies = self._proxy()
				async with session.get(
					f'https://rest.4based.com/api/1.0/file-stack/{post_id}',
					params=params,
					proxy=proxies,
					timeout=60
				) as response:
					if not response.ok:
						raise Exception(await _http_failure(response, f'Fetch media ID for {post_id}'))

					media_id = (await response.json()).get('vault_file_stack_id', None)
					if not media_id or media_id is None:
						raise Exception(f'No media ID found for {post_id}')

					success, msg = self.update(creator, {'media_id': media_id,'post_id':post_id})
					if not success:
						raise Exception(f'Error updating creator {creator_id} with media ID {media_id}: {msg}')

					return True, f'Successfully saved media ID {media_id} for creator {creator_id}'
		except Exception as e:
			return False, f'Error saving media ID {media_id} for creator {creator_id}: {str(e)}'

	async def upload_media(self, session, creator_id, media_id, creator_name, user_name,  caption, is_paid=False, price=0, proxy=None):
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

			async with session.post(
				f'https://rest.4based.com/api/1.0/user/{creator_id}/file-stack/',
				json=json_data,
				proxy=proxy,
				timeout=60
			) as response:
				if not response.ok:
					raise Exception(await _http_failure(response, f'Send media data to {user_name} by {creator_name}'))
				payload = await response.json()
				if not payload.get('complete', False):
					raise Exception(await _http_failure(response, f'Send media data to {user_name} by {creator_name}'))
				media_id = payload.get('_id')

			return True, media_id
		except Exception as e:
			return False, f'Error saving media for creator {creator_id}: {str(e)}'

	def _search_prefixes(self, max_length=3):
		letters = list(string.ascii_lowercase)
		random.shuffle(letters)
		prefixes = []
		for first in letters:
			prefixes.append(first)
			current = [first]
			for _ in range(2, max_length + 1):
				nxt = []
				for prefix in current:
					extras = list(string.ascii_lowercase)
					random.shuffle(extras)
					for letter in extras:
						term = prefix + letter
						prefixes.append(term)
						nxt.append(term)
				current = nxt
		return prefixes

	async def scrape_users(self, scraper, admin, task_id, count=40, offset=0, search=None, recipient_type='users'):
		if recipient_type not in ('users', 'creators', 'both'):
			recipient_type = 'users'
		roles = []
		if recipient_type in ('users', 'both'):
			roles.append(('client', 'scraped'))
		if recipient_type in ('creators', 'both'):
			roles.append(('creator', 'creator'))
		stats = {'search': search or '', 'new_users': 0, 'skipped': 0}
		try:
			success, task_status = Utils.check_task_status(task_id)
			if not success:raise Exception(task_status)
			if task_status['status'].lower() in ['cancelled', 'canceled']:
				return False, 'Task canceled', stats

			if not isinstance(scraper, dict) or not scraper.get('id'):
				return False, f'Scraper is not logged in: {scraper}', stats

			scraper_id = scraper.get('id')
			if not search:
				search = ''.join(random.choices(string.ascii_lowercase, k=2))
			stats['search'] = search
			Utils.write_log(f'Scraping users by {scraper_id} search={search} offset={offset}')
			client_msg = {'msg':f'Scraping users by {scraper_id} ({search})','status':'success','type':'message'}
			Utils.update_client(client_msg)

			async with _http_session(headers=scraper.get('headers')) as session:
				session.cookie_jar.update_cookies(scraper.get('cookies'))
				proxies = self._proxy(scraper.get('proxies'), scraper.get('reuse_ip', True))

				all_candidates = []
				seen_ids = set()
				for role, category in roles:
					params = {
						'offset': f'{offset}',
						'limit': f'{count}',
						'search': search,
						'sort': '{"follower_count":"desc"}' if category == 'creator' else '{"follower_count":"asc"}',
						'role': role,
					}
					if category == 'creator':
						params['verified'] = 'true'

					async with session.get(
						'https://rest.4based.com/api/1.0/user',
						params=params,
						proxy=proxies,
						timeout=60
					) as response:
						if response.status == 401:
							raise SessionExpired(await _http_failure(response, 'Fetch users'))
						if not response.ok:
							return False, await _http_failure(response, 'Fetch users'), stats

						try:
							users = await _response_json(response)
						except Exception as error:
							Utils.write_log(f'--- Search role={role} search={search} offset={offset} got non-JSON response: {error} ---')
							return False, f'{error}', stats
						if not isinstance(users, list):
							Utils.write_log(f'--- Search role={role} search={search} offset={offset} returned non-list {type(users).__name__}: {str(users)[:200]} ---')
							continue

						dropped = {'no_id': 0, 'creator_flag': 0, 'not_contactable': 0, 'duplicate': 0}
						kept = 0
						for user in users:
							if not user.get('_id'):
								dropped['no_id'] += 1
								continue
							if category != 'creator' and user.get('creator', False):
								dropped['creator_flag'] += 1
								continue
							if user.get('cold_communication_status') in ['actively_not_contactable', 'passively_not_contactable']:
								dropped['not_contactable'] += 1
								continue
							user_id = user.get('_id')
							if user_id in seen_ids:
								dropped['duplicate'] += 1
								continue
							seen_ids.add(user_id)
							kept += 1
							all_candidates.append(({
								'_id': user_id,
								'username': user.get('name') or user.get('username') or 'unknown',
							}, category))
						Utils.write_log(f'--- Search role={role} search={search} offset={offset} verified={params.get("verified", "-")}: {len(users)} returned, {kept} kept, dropped={dropped} ---')

				if not all_candidates:
					Utils.write_log(f'--- No valid users found for search={search} offset={offset} recipient_type={recipient_type} ---')
					return False, 'No valid users found', stats

				success, existing_ids = Utils.get_existing_user_ids(
					[user['_id'] for user, _category in all_candidates],
					admin=admin
				)
				if not success:
					return False, existing_ids, stats

				new_by_category = {'scraped': [], 'creator': []}
				for candidate, category in all_candidates:
					if candidate['_id'] not in existing_ids:
						new_by_category[category].append(candidate)
				new_users = new_by_category['scraped'] + new_by_category['creator']
				skipped = len(all_candidates) - len(new_users)
				stats['new_users'] = len(new_users)
				stats['skipped'] = skipped
				if skipped:
					client_msg = {'msg':f'Skipped {skipped} users because they already exist in the database','status':'success','type':'message'}
					Utils.update_client(client_msg)

				if not new_users:
					return False, f'No new users found for {scraper_id}', stats

				for category, group in new_by_category.items():
					if not group:
						continue
					success, msg = Utils.add_users(group, admin=admin, task_id=task_id, category=category)
					if not success:
						return False, msg, stats

				scraped_parts = []
				if new_by_category['scraped']:
					scraped_parts.append(f"{len(new_by_category['scraped'])} users")
				if new_by_category['creator']:
					scraped_parts.append(f"{len(new_by_category['creator'])} creators")
				return True, f'Scraped {" and ".join(scraped_parts)} by {scraper_id} ({search})', stats

		except SessionExpired:
			raise
		except Exception as e:
			return False, f'Error scraping users: {str(e)}', stats

	def _is_messageable(self, user, is_creator=False):
		if not user.get('_id'):
			return False
		if not is_creator and user.get('creator', False):
			return False
		if user.get('cold_communication_status') in ['actively_not_contactable','passively_not_contactable']:
			return False
		return True

	async def _prepare_sender(self, admin, task_id, creator, config):
		creator_data = creator['data']
		user = creator_data['details']['user']
		creator_name = user['name']
		caption_source = config.get('caption_source','creator')
		Utils.write_log(f'=== {config} ===')

		success, session_data = await self.login(
			admin,
			user['identifier'],
			user['password'],
			reuse_ip=self._reuse_ip(creator, config),
			task_id=task_id,
			category='users' if config.get('sender_type') == 'users' else 'creators',
			proxy_flush=config.get('proxy_flush', False)
		)
		if not success:raise Exception(session_data)

		captions = []
		if caption_source == 'creator':
			captions_file = os.path.join(configs_folder,creator['id'],'captions.txt')
			if not isfile(captions_file):raise Exception(f'Captions file does not exist for {creator_name}')

			with open(captions_file,'r',encoding='utf-8') as f:
				captions = [line.strip() for line in f.readlines() if line.strip()]
				if len(captions) < 1: raise ValueError('Captions can not be empty')
		elif caption_source == 'pool':
			captions = [line for line in config.get('caption_pool') or [] if line]
			if len(captions) < 1: raise ValueError('Caption pool can not be empty')

		if len(session_data.get('headers') or {}) < 1 or len(session_data.get('cookies') or {}) < 1:
			raise Exception(f'User {creator_name} does not have session data')

		return {
			'session_data': session_data,
			'creator_name': creator_name,
			'creator_id': user['_id'],
			'creator_internal_id': creator['id'],
			'captions': captions,
			'caption_source': caption_source,
			'universal_caption': config.get('caption',''),
			'price': config.get('price',0),
			'has_media': config.get('has_media',False),
			'media_args': {
				'has_media': config.get('has_media',False),
				'vault_media_id': creator_data.get('media_id',None),
				'is_paid': config.get('cost_type','free') != 'free',
				'price': config.get('price',0),
			},
		}

	def _pick_caption(self, sender):
		if sender['caption_source'] in ('creator', 'pool'):
			return random.choice(sender['captions'])
		return sender['universal_caption']

	async def _refresh_sender(self, admin, task_id, account, config, sender, session):
		user = account['data']['details']['user']
		success, session_data = await self.login(
			admin,
			user['identifier'],
			user['password'],
			reuse_ip=self._reuse_ip(account, config),
			task_id=task_id,
			category='users' if config.get('sender_type') == 'users' else 'creators',
			proxy_flush=config.get('proxy_flush', False),
			stale_token=_auth_token(sender['session_data'].get('headers'))
		)
		if not success:
			return False, session_data
		sender['session_data'] = session_data
		session.headers.update(session_data.get('headers') or {})
		session.cookie_jar.update_cookies(session_data.get('cookies') or {})
		return True, session_data

	async def get_chat_state(self, session, creator_id, chat_id, recipient_id, proxies):
		async with session.get(
			f'https://rest.4based.com/api/1.0/user/{creator_id}/chat/{chat_id}',
			params={'with_last_message': 'true', 'with_users': 'true'},
			proxy=proxies,
			timeout=60
		) as response:
			if response.status == 401:
				raise SessionExpired(await _http_failure(response, f'Fetch chat {chat_id}'))
			if not response.ok:
				return False, await _http_failure(response, f'Fetch chat {chat_id}')
			chat = await response.json()

		recipient = next((user for user in chat.get('users') or [] if user.get('_id') == recipient_id), {})
		return True, {
			'last_message': chat.get('last_message') or {},
			'recipient_online': bool(recipient.get('is_online')),
		}

	def _resend_decision(self, last_message, stored_chat, creator_id, hours):
		# Only messages sent to the user count; user replies and 4based auto_* messages fall back to our DB record.
		last_type = str(last_message.get('type') or '')
		from_creator = last_message.get('user_id') == creator_id and not last_type.startswith('auto_')
		if from_creator:
			stored_id = stored_chat.get('last_message_id')
			if stored_id and last_message.get('_id') != stored_id:
				return True, 'last sent message was manual'
			reference = last_message.get('created_at')
		else:
			reference = stored_chat.get('created_at')

		sent_at = _parse_utc(reference)
		if sent_at is None:
			return True, 'no previous sent message found'
		age_hours = (datetime.utcnow() - sent_at).total_seconds() / 3600
		if age_hours < hours:
			return False, f'last sent {age_hours:.1f}h ago < {hours}h'
		return True, f'last sent {age_hours:.1f}h ago'

	async def _send_chat_message(self, session, creator_id, chat_id, caption, username, creator_name, proxies, has_media=False, vault_media_id=None, is_paid=False, price=0):
		sent_media_id = None
		if has_media and vault_media_id:
			success, sent_media_id = await self.upload_media(
				session,
				creator_id,
				vault_media_id,
				creator_name,
				username,
				caption,
				is_paid=is_paid,
				price=price,
				proxy=proxies
			)
			if not success:
				return False, f'Failed to upload media for {username} by {creator_name}: {sent_media_id}'

		json_data = {
			'message': caption,
			'sender_status': 'sent',
			'local_id': str(uuid.uuid4()),
		}
		if sent_media_id:json_data['file_stack_id'] = sent_media_id

		async with session.post(
			f'https://rest.4based.com/api/1.0/user/{creator_id}/chat/{chat_id}/message',
			json=json_data,
			proxy=proxies,
			timeout=60
		) as response:
			if response.status == 401:
				raise SessionExpired(await _http_failure(response, f'Send message to {username} by {creator_name}'))
			if not response.ok:
				return False, await _http_failure(response, f'Send message to {username} by {creator_name}')
			try:
				sent = await response.json()
			except Exception:
				sent = {}
		return True, (sent or {}).get('_id')

	async def send_messages(self,admin,task_id,creator,config,maxworkers,shard_index=None,shard_count=None):
		try:
			success,task_status = Utils.check_task_status(task_id)
			if not success:raise Exception(task_status)
			if task_status['status'].lower() in ['cancelled','canceled']:return False,  'Task canceled'

			account = creator
			sender = await self._prepare_sender(admin, task_id, creator, config)
			creator = sender['session_data']
			creator_name = sender['creator_name']
			creator_id = sender['creator_id']
			creator_internal_id = sender['creator_internal_id']
			has_media, price, media_args = sender['has_media'], sender['price'], sender['media_args']

			recipient_type = config.get('recipient_type', 'users')
			if recipient_type not in ('users', 'creators', 'both'):
				recipient_type = 'users'
			recipient_label = {'users': 'users', 'creators': 'creators', 'both': 'users and creators'}[recipient_type]

			success, users_msg, total_users = Utils.get_users(admin, recipient_type=recipient_type)
			if not success:raise Exception(users_msg)

			offset = account['data'].get('message_offset', 0)
			if offset >= total_users:
				offset = 0

			client_msg = {'msg': f'Fetching {recipient_label} from DB (unmessaged by {creator_name}) offset {offset}','status':'success','type':'message'}
			Utils.update_client(client_msg)
			Utils.write_log(f'--- Fetching {recipient_label} from DB (unmessaged by {creator_name}) offset {offset} ---')

			async with _http_session(headers=creator.get('headers')) as session:
				session.cookie_jar.update_cookies(creator.get('cookies'))
				proxies = self._proxy(creator.get('proxies'), creator.get('reuse_ip', True))

				users, found_users = [], 0
				while found_users < maxworkers:
					success, new_users = Utils.get_unmessaged_users(creator_internal_id, limit=maxworkers, offset=offset, recipient_type=recipient_type, shard_index=shard_index, shard_count=shard_count)
					if not success:raise Exception(new_users)
					if not new_users:break
					users.extend(new_users)
					found_users += len(new_users)
					offset += maxworkers

				if len(users) == 0:
					return False, f'No unmessaged {recipient_label} found for {creator_name}'

				success_messages, conflict_logged, session_refreshed = 0, False, False
				for user in users:
					username = user.get('name') or user.get('username') or 'unknown'
					recipient_id = user.get('_id') or user.get('id')
					try:
						success,task_status = Utils.check_task_status(task_id)
						if not success:raise Exception(task_status)
						if task_status['status'].lower() in ['cancelled','canceled']:
							return False, 'Task canceled'
						if not recipient_id:
							Utils.write_log(f'--- Skipping user without id: {user} ---')
							continue
						if recipient_id == creator_id:
							Utils.write_log(f'--- Skipping {username}; creator cannot message themselves ---')
							continue

						caption = self._pick_caption(sender)

						# create a chat ID for the user
						async with session.post(
							f'https://rest.4based.com/api/1.0/user/{creator_id}/chat/user/{recipient_id}',
							proxy=proxies,
							timeout=60
						) as response:
							if response.status == 401:
								raise SessionExpired(await _http_failure(response, f'Create chat with {username} by {creator_name}'))
							if not response.ok and response.status != 409:
								err_text = await _http_failure(response, f'Send message to {username} by {creator_name}')
								client_msg = {'msg':err_text,'status':'error','type':'message'}
								Utils.update_client(client_msg)
								Utils.write_log(f'--- {err_text} ---')
								continue

							elif response.status == 409:
								if not conflict_logged:
									body = ' '.join((await response.text() or '').split())[:500]
									Utils.write_log(f'User {username} already has a chat with {creator_name}, skipping... 409 body: {body}')
									conflict_logged = True
								else:
									Utils.write_log(f'User {username} already has a chat with {creator_name}, skipping...')
								continue

							chat_data = await response.json()
							message_id = chat_data.get('_id', None)
							if not message_id:
								client_msg = {'msg':f'No chat ID found for user {username}','status':'error','type':'message'}
								Utils.update_client(client_msg)
								Utils.write_log(f'--- No chat ID found for user {username} ---')
								continue

						success, sent_id = await self._send_chat_message(session, creator_id, message_id, caption, username, creator_name, proxies, **media_args)
						if not success:
							Utils.write_log(f'--- {sent_id} ---')
							client_msg = {'msg':sent_id,'status':'error','type':'message'}
							Utils.update_client(client_msg)
							continue

						success, msg = Utils.add_message(
							message_id,
							admin,
							creator_internal_id,
							creator_name,
							recipient_id,
							username,
							has_media,
							f'https://4based.com/chat/{message_id}/conversation',
							'sent',
							caption,
							price,
							last_message_id=sent_id
						)

						if not success:
							Utils.write_log(f'Error adding message to database for {username} by {creator_name}: {msg}')
							client_msg = {'msg':f'Error adding message to database for {username} by {creator_name}: {msg}','status':'error','type':'message'}
							Utils.update_client(client_msg)
							continue

						Utils.write_log(f'=== Successfully sent a message to {username} by {creator_name} ===')
						client_msg = {'msg':f'Successfully sent a message to {username} by {creator_name}','status':'success','type':'message'}
						success,msg = Utils.update_client(client_msg)
						if not success:Utils.write_log(msg)

						success_messages += 1
						await asyncio.sleep(random.randint(5, 10))  # Sleep to avoid rate limiting

					except SessionExpired as e:
						if session_refreshed:
							return False, f'Session for {creator_name} was rejected again after relogin: {e}'
						Utils.write_log(f'--- Session for {creator_name} expired, refreshing: {e} ---')
						success, msg = await self._refresh_sender(admin, task_id, account, config, sender, session)
						if not success:
							return False, f'Could not refresh session for {creator_name}: {msg}'
						session_refreshed = True
						continue

					except Exception as e:
						Utils.write_log(str(e))
						client_msg = {'msg':f'Failed to message user {username}: {e}','status':'error','type':'message'}
						Utils.update_client(client_msg)
						continue

			success, msg = Creator().update(account, {'message_offset': offset})
			if not success:Utils.write_log(msg)

			if success_messages > 0:
				Utils.write_log(f'=== Successfully sent messages to {success_messages} users by {creator_name} ===')
				client_msg = {'msg':f'Successfully sent messages to {success_messages} {recipient_label} by {creator_name}','status':'success','type':'message'}
				success,msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)
				return True, f'Successfully sent messages to {success_messages} {recipient_label} by {creator_name}'
			return False, f'{creator_name} could not send any messages to {recipient_label}'

		except ValueError as ve:
			return False, f'Value error while sending messages to users for {creator.get("id")}: {str(ve)}'

		except Exception as e:
			return False,f'Error sending messages to users for {creator.get("id")}: {str(e)}'

	async def send_follow_ups(self,admin,task_id,creator,config,maxworkers):
		try:
			success,task_status = Utils.check_task_status(task_id)
			if not success:raise Exception(task_status)
			if task_status['status'].lower() in ['cancelled','canceled']:return False,  'Task canceled'

			sender = await self._prepare_sender(admin, task_id, creator, config)
			session_data = sender['session_data']
			creator_name = sender['creator_name']
			creator_id = sender['creator_id']
			creator_internal_id = sender['creator_internal_id']
			online_only = config.get('online_only', True)
			hours = int(config.get('resend_after_hours') or 24)

			recipient_type = config.get('recipient_type', 'users')
			if recipient_type not in ('users', 'creators', 'both'):
				recipient_type = 'users'
			success, candidates = Utils.get_resend_candidates(creator_internal_id, hours, limit=maxworkers * 5, recipient_type=recipient_type)
			if not success:raise Exception(candidates)
			if not candidates:
				return True, f'No chats older than {hours}h to follow up for {creator_name}'

			client_msg = {'msg': f'Checking {len(candidates)} chats for follow-ups by {creator_name}','status':'success','type':'message'}
			Utils.update_client(client_msg)
			Utils.write_log(f'--- Checking {len(candidates)} chats for follow-ups by {creator_name} ---')

			sent_follow_ups, failed_follow_ups, session_refreshed = 0, 0, False
			async with _http_session(headers=session_data.get('headers')) as session:
				session.cookie_jar.update_cookies(session_data.get('cookies'))
				proxies = self._proxy(session_data.get('proxies'), session_data.get('reuse_ip', True))

				for user in candidates:
					if sent_follow_ups >= maxworkers:
						break
					username = user.get('username') or 'unknown'
					recipient_id = user.get('id')
					try:
						success,task_status = Utils.check_task_status(task_id)
						if not success:raise Exception(task_status)
						if task_status['status'].lower() in ['cancelled','canceled']:
							return False, 'Task canceled'

						success, stored_chat = Utils.get_chat_for_recipient(creator_internal_id, recipient_id)
						if not success:raise Exception(stored_chat)
						if not stored_chat:
							continue

						chat_id = stored_chat['chat_id']
						success, chat_state = await self.get_chat_state(session, creator_id, chat_id, recipient_id, proxies)
						if not success:
							Utils.write_log(f'--- Skipping {username}; chat fetch failed: {chat_state} ---')
							client_msg = {'msg':chat_state,'status':'error','type':'message'}
							Utils.update_client(client_msg)
							failed_follow_ups += 1
							continue

						if online_only and not chat_state['recipient_online']:
							Utils.write_log(f'--- Skipping {username}; not online ---')
							continue

						should_send, reason = self._resend_decision(chat_state['last_message'], stored_chat, creator_id, hours)
						if not should_send:
							Utils.write_log(f'--- Skipping {username}; {reason} ---')
							continue

						caption = self._pick_caption(sender)
						success, sent_id = await self._send_chat_message(session, creator_id, chat_id, caption, username, creator_name, proxies, **sender['media_args'])
						if not success:
							Utils.write_log(f'--- {sent_id} ---')
							client_msg = {'msg':sent_id,'status':'error','type':'message'}
							Utils.update_client(client_msg)
							failed_follow_ups += 1
							continue

						success, msg = Utils.update_last_sent(chat_id, sent_id, caption)
						if not success:Utils.write_log(msg)

						Utils.write_log(f'=== Followed up {username} by {creator_name} ({reason}) ===')
						client_msg = {'msg':f'Followed up {username} by {creator_name} ({reason})','status':'success','type':'message'}
						Utils.update_client(client_msg)
						sent_follow_ups += 1
						await asyncio.sleep(random.randint(5, 10))  # Sleep to avoid rate limiting

					except SessionExpired as e:
						if session_refreshed:
							return False, f'Session for {creator_name} was rejected again after relogin: {e}'
						Utils.write_log(f'--- Session for {creator_name} expired, refreshing: {e} ---')
						success, msg = await self._refresh_sender(admin, task_id, creator, config, sender, session)
						if not success:
							return False, f'Could not refresh session for {creator_name}: {msg}'
						session_refreshed = True
						continue

					except Exception as e:
						Utils.write_log(str(e))
						client_msg = {'msg':f'Failed to follow up user {username}: {e}','status':'error','type':'message'}
						Utils.update_client(client_msg)
						failed_follow_ups += 1
						continue

			if sent_follow_ups > 0:
				return True, f'Sent {sent_follow_ups} follow-ups by {creator_name}'
			if failed_follow_ups > 0:
				return False, f'{creator_name} could not send any follow-ups ({failed_follow_ups} failed)'
			return True, f'{creator_name} had no chats ready for a follow-up'

		except ValueError as ve:
			return False, f'Value error while sending follow-ups for {creator.get("id")}: {str(ve)}'

		except Exception as e:
			return False, f'Error sending follow-ups for {creator.get("id")}: {str(e)}'

	async def login(self,admin,email,password,reuse_ip=True,task_id=None,category='creators',proxy_flush=False,stale_token=None):
		lock = _session_lock(f'{admin}:{email}')
		while not lock.acquire(blocking=False):
			await asyncio.sleep(0.5)
		try:
			attempts = 3
			last_error = None
			for attempt in range(1, attempts + 1):
				success, result = await self._try_login(
					admin, email, password, reuse_ip=reuse_ip, task_id=task_id, category=category, proxy_flush=proxy_flush, stale_token=stale_token
				)
				if success:
					return success, result
				last_error = result
				if not _is_retryable_error(result) or attempt == attempts:
					return False, result
				Utils.write_log(f'Retrying login for {email} ({attempt}/{attempts}): {result}')
				await asyncio.sleep(min(2 * attempt, 5))
			return False, last_error
		finally:
			lock.release()

	async def _try_login(self,admin,email,password,reuse_ip=True,task_id=None,category='creators',proxy_flush=False,stale_token=None):
		async with _http_session() as session:
			try:
				if task_id:
					success,task_status = Utils.check_task_status(task_id)
					if not success:raise Exception(task_status)
					if task_status.get('status', '').lower() in ['cancelled','canceled']:
						return False, 'Task canceled'

				success,user = Utils.check_creator(email,admin)
				if not success:raise Exception(user)

				creator_id = user.get('id', None)
				user_data = user.get('data', {})
				new_user = creator_id is None

				use_stored = reuse_ip and not proxy_flush
				proxies = self._proxy(user_data.get('proxies') if use_stored else None, use_stored)

				stored_token = _auth_token(user_data.get('headers'))
				if stale_token and stored_token and stored_token != stale_token:
					Utils.write_log(f'Session for {email} was already refreshed by another task, reusing it')
					user_data['id'] = creator_id
					user_data['status'] = 'Online'
					return True, user_data

				# reuse an existing session if it is still valid
				if not stale_token and not new_user and user_data.get('headers') and user_data.get('cookies'):
					session.headers.update(user_data.get('headers', {}))
					session.cookie_jar.update_cookies(user_data.get('cookies', {}))
					params = {
						'with_user_pivot_interaction': 'true',
					}

					async with session.get(
						f"https://rest.4based.com/api/1.0/user/name/{user_data['details']['user']['name']}",
						params=params,
						proxy=proxies,
						timeout=60
					) as response:
						if response.status == 200:
							user_data['id'] = creator_id
							user_data['status'] = 'Online'
							if proxy_flush:
								user_data['proxies'] = proxies
								user_data['reuse_ip'] = False
							return True, user_data

				json_data = {
					'identifier': email,
					'password': password,
					'locale': 'en',
				}

				session.headers.update(self.headers)
				session.headers.update({
					'user-agent': Utils.generate_user_agent('android',1),
					'x-auth-resource': self.generate_sensor_data(),
				})

				async with session.post(
					'https://rest.4based.com/api/1.0/auth/login',
					json=json_data,
					proxy=proxies,
					timeout=60
				) as response:
					if response.status in (400, 401):
						try:
							payload = await response.json()
						except Exception:
							payload = {}
						if isinstance(payload, dict) and 'password not correct' in payload.values():
							return False, 'Credentials not correct'
						return False, await _http_failure(response, f'Login for {email}')
					if not response.ok:
						return False, await _http_failure(response, f'Login for {email}')

					data = await _response_json(response)
					token,auth_resource = data['credentials']['token'],data['credentials']['resource']

					user_data['status'] = 'Online'
					user_data['details'] = data
					user_data['details']['user']['password'] = password
					avatar = user_data['details']['user']['avatar']

					session.headers.update({
						'x-auth-resource': auth_resource,
						'x-auth-token':token
					})

					user_data['headers'] = dict(session.headers)
					user_data['cookies'] = {
						key: str(value) for key, value in session.cookie_jar.filter_cookies('https://rest.4based.com').items()
					}

					if avatar is not None:
						image_url = f'https://pic.4based.com/preview/{avatar["code"]}/{avatar["_id"]}/300x300.{avatar["extension"]}'
						user_data['details']['user']['picture'] = image_url

					user_data['proxies'] = proxies
					user_data['reuse_ip'] = False if proxy_flush else reuse_ip

				if new_user:
					creator_id = str(uuid.uuid4()).upper()[:8]
					success,msg = Utils.add_creator(creator_id,email,user_data,admin,category=category,task_id=task_id)
					if not success:raise Exception(msg)

					images_folder = os.path.join(configs_folder,creator_id,'images')
					videos_folder = os.path.join(configs_folder,creator_id,'videos')
					captions_file = os.path.join(configs_folder,creator_id,'captions.txt')

					os.makedirs(images_folder,exist_ok=True)
					os.makedirs(videos_folder,exist_ok=True)
					with open(captions_file, 'w') as file:file.write("")
				else:
					success,msg = Utils.update_creator(creator_id,email,user_data)
					if not success:raise Exception(msg)

				user_data['id'] = creator_id
				await session.close()
				return True, user_data

			except NetworkError as e:
				Utils.write_log(f'Login network error on {email}: {e}')
				return False, f'Login failed for {email}: {e}'
			except Exception as error:
				tb = traceback.format_exc()
				Utils.write_log(f'Error in login {error} on {email}\n{tb}')
				return False, f'Error in login on {email}: {error}'

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


	async def add_creators(self,admin,task,creators,category):
		task_status,task_msg,completed,fails = 'running',f'Started logging in creators for {task["id"]}',0,0
		task_id = task['id']
		try:
			Utils.write_log(f'=== Add {category} started for {task["id"]} ===')

			async def login_creator(creator):
				success,current_task = Utils.check_task_status(task_id)
				if not success:
					raise Exception(current_task)
				if current_task['status'].lower() in ['cancelled','canceled']:
					return False, 'Task canceled'
				ok, msg = await Creator().login(admin, creator['email'], creator['password'], task_id=task_id, category=category)
				if not ok:
					return False, f"{creator.get('email')}: {msg}"
				return True, msg

			results = await asyncio.gather(
				*[login_creator(creator) for creator in creators],
				return_exceptions=True
			)

			for item in results:
				if isinstance(item, Exception):
					success,result = False, str(item)
				elif isinstance(item, (tuple, list)) and len(item) == 2:
					success,result = item
				else:
					success,result = False, str(item)

				if success:
					completed += 1
					task_msg = f'{completed} {category} added so far on task:{task_id}'
					Utils.push_task_update(task, 'running', task_msg, client_status='success')
				elif result == 'Task canceled':
					task_status = 'canceled'
					task_msg = f'{result} task:{task_id}'
					Utils.push_task_update(task, task_status, task_msg)
					break
				else:
					fails += 1
					task_msg = str(result)
					Utils.push_task_update(task, 'running', task_msg, client_status='error')

				Utils.write_log(task_msg)

		except Exception as error:
			Utils.write_log(error)
			task_status = 'failed'
			task_msg = f'Error adding creators on {task_id}: {error}'
			Utils.push_task_update(task, task_status, task_msg)

		finally:
			if task_status == 'canceled':
				pass
			elif task_status == 'failed':
				pass
			elif completed == len(creators) and len(creators) > 0:
				task_status = 'success'
				task_msg = f'{task_id} successful'
			elif fails > 0:
				task_status = 'failed'
			else:
				task_status = 'completed'
				task_msg = f'{completed} items successful task:{task_id}'

			Utils.push_task_update(task, task_status, task_msg)


	async def start_messaging(self,task,maxworkers=10,follow_up=False):
		task_status,task_msg = 'failed',f'Started messaging for {task["id"]}'
		try:
			admin = task['admin']
			task_id = task['id']
			config = task['config']
			selected_creators = config.get('selected_creators') or config.get('select-creators', [])
			time_between = config.get('time_between', 60)
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

			# Sender is chosen independently of recipients: send as creator
			# accounts (default, legacy tasks) or as user accounts. The
			# selected/universal source was already resolved into
			# selected_senders by the route; old tasks only have
			# selected_creators, which now holds that same selection.
			sender_type = config.get('sender_type', 'creators')
			if sender_type not in ('creators', 'users'):
				sender_type = 'creators'
			sender_category = 'users' if sender_type == 'users' else 'creators'
			selected_senders = config.get('selected_senders') or selected_creators

			success,creators,total_creators = Utils.get_creators(admin=admin,limit=100,selected_creators=selected_senders,category=sender_category)
			if not success:raise Exception(creators)

			while len(creators) < total_creators:
				success,page,total_creators = Utils.get_creators(admin=admin,limit=100,offset=len(creators),selected_creators=selected_senders,category=sender_category)
				if not success:raise Exception(page)
				if not page: break
				creators += page
			if not creators:
				raise Exception(f'No {sender_type[:-1]} accounts available to send from on {task_id}; add one first')

			if config.get('proxy_flush'):
				flushed = Creator().apply_proxy_flush(creators)
				Utils.write_log(f'Proxy flush enabled: reuse_ip disabled for {flushed} accounts with stored proxies')
				client_msg = {'msg':f'Proxy flush enabled: reuse_ip disabled for {flushed} accounts with stored proxies','status':'success','type':'message'}
				Utils.update_client(client_msg)

			Utils.write_log(f'=== {"Follow-ups" if follow_up else "Messaging"} started for {task_id} ===')
			send_method = 'send_follow_ups' if follow_up else 'send_messages'
			# Sharding only applies to first messages: each sender gets a
			# disjoint slice of the target pool. Follow-ups are per-sender
			# chats, so they are never sharded.
			shard = bool(config.get('shard_targets')) and not follow_up and len(creators) > 1

			while True:
				success,task_status = Utils.check_task_status(task_id)
				if not success:raise Exception(task_status)
				if task_status['status'].lower() in ['cancelled','canceled']:
					break

				if follow_up:
					calls = [Creator().send_follow_ups(admin, task_id, creator, config, maxworkers) for creator in creators]
				else:
					calls = [
						Creator().send_messages(
							admin, task_id, creator, config, maxworkers,
							shard_index=index if shard else None,
							shard_count=len(creators) if shard else None
						)
						for index, creator in enumerate(creators)
					]
				results = await asyncio.gather(*calls, return_exceptions=True)

				for item in results:
					if isinstance(item, Exception):
						success,result = False, str(item)
					elif isinstance(item, (tuple, list)) and len(item) == 2:
						success,result = item
					else:
						success,result = False, str(item)

					if not success:
						label = 'Error sending follow-ups' if follow_up else 'Error messaging users'
						client_msg = {'msg':f'{label} on {task_id}: {result}','status':'error','type':'message'}
						success,msg = Utils.update_client(client_msg)
					else:
						label = 'Follow-ups' if follow_up else 'Success messaging users'
						client_msg = {'msg':f'{label} on {task_id}: {result}','status':'success','type':'message'}
						success,msg = Utils.update_client(client_msg)

					Utils.write_log(f'=== {result} ===')

				wait_message = f'Waiting for {time_message[str(time_between)]} before sending another batch of {"follow-ups" if follow_up else "messages"}'
				Utils.write_log(wait_message)
				client_msg = {'msg':wait_message,'status':'success','type':'message'}
				success,msg = Utils.update_client(client_msg)

				sleep_time = 10
				for _ in range(int(int(time_between) / sleep_time)):
					success,task_status = Utils.check_task_status(task_id)
					if not success:raise Exception(task_status)
					if task_status['status'].lower() in ['cancelled','canceled']:
						raise Cancelled(task_status)
					await asyncio.sleep(sleep_time)

		except Cancelled:
			task_status = task_status['status'] if isinstance(task_status, dict) else task_status
			if str(task_status).lower() in ['cancelled', 'canceled']:
				client_msg = {'msg':f'Task | {task_id} has been cancelled','status':'error','type':'message'}
				success,msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)
				Utils.write_log(f'Task | {task_id} was stopped')
			else:
				Utils.write_log(f'Task | {task_id} finished operation')

		except Exception as error:
			Utils.write_log(error)
			task_status = 'failed'
			task_msg = f'Error in messaging | {task_id}: {error}'
			client_msg = {'msg':task_msg,'status':'error','type':'message'}
			success,msg = Utils.update_client(client_msg)
			if not success:Utils.write_log(msg)

			success,msg = Utils.update_task(task_id,{
				'status':task_status,
				'message':task_msg
			})
			task_data = task
			task_data.update({'updated':str(datetime.now()), 'status':task_status})
			success,msg = Utils.update_client({'task':task_data,'type':'task'})
			if not success:Utils.write_log(msg)

		finally:
			task_status = task_status['status'] if isinstance(task_status, dict) else task_status
			if str(task_status).lower() in ['cancelled', 'canceled']:
				client_msg = {'msg':f'Task | {task_id} has been cancelled','status':'error','type':'message'}
				success,msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)
				Utils.write_log(f'Task | {task_id} was stopped')
			else:
				Utils.write_log(f'Task | {task_id} finished operation')

	async def _scrape_with_refresh(self, admin, task_id, config, target_scraper, scraper, count, offset, search):
		try:
			return await Creator().scrape_users(scraper, admin, task_id, count=count, offset=offset, search=search, recipient_type=config.get('recipient_type', 'users'))
		except SessionExpired as error:
			email = target_scraper['email']
			Utils.write_log(f'--- Session for {email} expired, refreshing: {error} ---')
			success, scraper = await Creator().login(
				admin,
				email,
				target_scraper['data']['details']['user']['password'],
				reuse_ip=Creator()._reuse_ip(target_scraper, config),
				task_id=task_id,
				category='users',
				proxy_flush=config.get('proxy_flush', False),
				stale_token=_auth_token(scraper.get('headers'))
			)
			if not success:
				return False, f'Could not refresh session for {email}: {scraper}', {'search': search, 'new_users': 0, 'skipped': 0}
			try:
				return await Creator().scrape_users(scraper, admin, task_id, count=count, offset=offset, search=search, recipient_type=config.get('recipient_type', 'users'))
			except SessionExpired as error:
				return False, f'Session for {email} was rejected again after relogin: {error}', {'search': search, 'new_users': 0, 'skipped': 0}

	async def start_scraping(self, task):
		task_status, task_msg = 'failed', f'Started scraping for {task["id"]}'
		try:
			admin = task['admin']
			task_id = task['id']
			config = task['config']
			time_between = config.get('time_between', 60)
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

			success, scrapers, total_scrapers = Utils.get_creators(admin=admin, limit=100, category='users')
			if not success:raise Exception(scrapers)
			if total_scrapers < 1: raise Exception('Scrapers can not be empty')

			while len(scrapers) < total_scrapers:
				success, page, total_scrapers = Utils.get_creators(admin=admin, limit=100, offset=len(scrapers), category='users')
				if not success: raise Exception(page)
				if not page: break
				scrapers += page

			if config.get('proxy_flush'):
				flushed = Creator().apply_proxy_flush(scrapers)
				Utils.write_log(f'Proxy flush enabled: reuse_ip disabled for {flushed} accounts with stored proxies')
				client_msg = {'msg':f'Proxy flush enabled: reuse_ip disabled for {flushed} accounts with stored proxies','status':'success','type':'message'}
				Utils.update_client(client_msg)

			Utils.write_log(f'=== Scraping started for {task_id} ===')

			offset, i = 0, 0
			count = config.get('max_actions', 40)
			prefixes = Creator()._search_prefixes()
			prefix_i = 0

			while True:
				success, task_status = Utils.check_task_status(task_id)
				if not success:raise Exception(task_status)
				if task_status['status'].lower() in ['cancelled', 'canceled']:
					break

				target_scraper = scrapers[i]
				email = target_scraper.get('email') or target_scraper.get('id')
				success, scraper = await Creator().login(
					admin,
					target_scraper['email'],
					target_scraper['data']['details']['user']['password'],
					reuse_ip=Creator()._reuse_ip(target_scraper, config),
					task_id=task_id,
					category='users',
					proxy_flush=config.get('proxy_flush', False)
				)

				if not success:
					result = scraper if isinstance(scraper, str) else f'Login failed for {email}'
					client_msg = {'msg': f'Error scraping users on {task_id}: {result}', 'status': 'error', 'type': 'message'}
					Utils.update_client(client_msg)
					Utils.write_log(f'=== {result} ===')
					i = i + 1 if i < len(scrapers) - 1 else 0
					await asyncio.sleep(5)
					continue

				search = prefixes[prefix_i]
				scrape_ok, result, stats = await self._scrape_with_refresh(
					admin, task_id, config, target_scraper, scraper, count, offset, search
				)

				if not scrape_ok:
					client_msg = {'msg': f'Error scraping users on {task_id}: {result}', 'status': 'error', 'type': 'message'}
					Utils.update_client(client_msg)
				else:
					client_msg = {'msg': result, 'status': 'success', 'type': 'message'}
					Utils.update_client(client_msg)

				Utils.write_log(f'=== {result} ===')

				wait_message = f'Waiting for {time_message[str(time_between)]} before scraping another batch of users'
				Utils.write_log(wait_message)
				client_msg = {'msg': wait_message, 'status': 'success', 'type': 'message'}
				Utils.update_client(client_msg)

				sleep_time = 10
				for _ in range(int(int(time_between) / sleep_time)):
					success, task_status = Utils.check_task_status(task_id)
					if not success:raise Exception(task_status)
					if task_status['status'].lower() in ['cancelled', 'canceled']:
						raise Cancelled(task_status)
					await asyncio.sleep(sleep_time)

				new_users = stats.get('new_users', 0)
				skipped = stats.get('skipped', 0)
				offset = offset + count
				advance = (
					not scrape_ok
					or new_users == 0
					or len(search) == 1
					or skipped >= new_users
					or offset >= 400
				)
				if advance:
					offset = 0
					prefix_i += 1
					if prefix_i >= len(prefixes):
						prefixes = Creator()._search_prefixes()
						prefix_i = 0
				i = i + 1 if i < len(scrapers) - 1 else 0

		except Cancelled:
			task_status = task_status['status'] if isinstance(task_status, dict) else task_status
			if str(task_status).lower() in ['cancelled', 'canceled']:
				client_msg = {'msg': f'Task | {task_id} has been cancelled', 'status': 'error', 'type': 'message'}
				success, msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)
				Utils.write_log(f'Task | {task_id} was stopped')
			else:
				Utils.write_log(f'Task | {task_id} finished operation')

		except Exception as e:
			Utils.write_log(e)
			task_status = 'failed'
			task_msg = f'Error in scraping | {task_id}: {e}'
			client_msg = {'msg': task_msg, 'status': 'error', 'type': 'message'}
			success, msg = Utils.update_client(client_msg)
			if not success:Utils.write_log(msg)

			success, msg = Utils.update_task(task_id, {'status': task_status, 'message': task_msg})
			task_data = task
			task_data.update({'updated': str(datetime.now()), 'status': task_status})
			success, msg = Utils.update_client({'task': task_data, 'type': 'task'})
			if not success:Utils.write_log(msg)

		finally:
			task_status = task_status['status'] if isinstance(task_status, dict) else task_status
			if str(task_status).lower() in ['cancelled', 'canceled']:
				client_msg = {'msg': f'Task | {task_id} has been cancelled', 'status': 'error', 'type': 'message'}
				success, msg = Utils.update_client(client_msg)
				if not success:Utils.write_log(msg)
				Utils.write_log(f'Task | {task_id} was stopped')
			else:
				Utils.write_log(f'Task | {task_id} finished operation')
