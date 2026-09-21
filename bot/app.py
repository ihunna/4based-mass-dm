from app_configs import *
from utils import Utils
from actions import Creator,_4BASED

success, msg = Utils.create_tables()
if not success:
    Utils.write_log(msg)

def run_async_coroutine(coroutine):
    """Run an async coroutine synchronously, handling existing event loops."""
    try:
        return asyncio.run(coroutine)
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            loop = asyncio.get_event_loop()
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                return future.result()
            return loop.run_until_complete(coroutine)
        raise

@socketio.on('connect')
def handle_connect():
    socketio.emit('connection','connection successful')
    # Utils.write_log('websocket connected')
    
@app.before_request
def before_request():
    if app_prefix:
        request.environ['SCRIPT_NAME'] = app_prefix
    g.host = host
    g.app_prefix = app_prefix
    g.app_name = app_name
    if app_logo.startswith(('http://', 'https://', '/')):
        g.app_logo = app_logo
    else:
        g.app_logo = url_for('static', filename=app_logo)

@app.after_request
def after_request(response):
    """Ensure responses aren't cached"""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response

@app.route('/',methods=['GET','POST'])
@login_required
def index():
    g.page = 'dashboard'
    admin = session['USER']['id']

    success, creators, total_creators = Utils.get_creators(admin=admin,limit=5)
    if not success:raise Exception(creators)

    success, messages, total_messages = Utils.get_messages(admin=admin,limit=5)
    if not success:raise Exception(messages)

    success, tasks, total_tasks = Utils.get_tasks(admin=admin,limit=5)
    if not success:raise Exception(tasks)

    stats = {
        'tasks':total_tasks,
        'messages':total_messages,
        'creators':total_creators
    }

    return render_template(
        'index.html',
        stats=stats,
        tasks=tasks,
        messages=messages,
        creators=creators)

@app.route('/admins',methods=['GET'])
@login_required
def admins():
    try:
        g.page = 'admins'
        action = request.args.get('action')
        page = request.args.get('page',1, type=int)
        tab = request.args.get('tab','all')

        per_page = 20
        offset = (page - 1) * per_page
        
        if action == 'view-item':
            item = request.args.get('admin')
            
            success, admin,_ = Utils.get_admins(multiple=False,keyword='id',admin=item)
            if success:return render_template('view-item.html', type='admin', item=admin)

            Utils.write_log(admin)
            return render_template('view-item.html', action=404)
        
        if action == 'edit-admin':
            g.page = action
            admin_id = request.args.get('admin')
            success, admin,_ = Utils.get_admins(multiple=False,keyword='id',admin=admin_id)
            
            if not success:
                Utils.write_log(admin)
                return render_template('view-item.html', action=404)
            
            return render_template('admins.html',action=action,admin=admin)
        
        if tab and tab == 'me':
            item = session['USER']['id']
            success, admin,_ = Utils.get_admins(multiple=False,keyword='id',admin=item)
            admins,total_admins = [admin],1

        else:success,admins,total_admins = Utils.get_admins(limit=per_page,offset=offset)
        
        next_page = page + 1 if page  < total_admins / per_page else page
        prev_page = page - 1 if page  > 1 else page
        current_page = offset + len(admins)

        if success:return render_template('admins.html', 
                    action=action, 
                    tab=tab,
                    admins=admins,
                    session_admin=session['USER'],
                    total_admins=total_admins,
                    next_page=next_page,
                    prev_page=prev_page,
                    current_page=current_page)
        
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/admins/<action>',methods=['POST'])
@login_required
@check_role
def handle_admin(action):
    try:
        if action == 'update':
            data = request.get_json()
            email = data['email']
            password = data['password']
            role = data['role']
            status = data['status']
            admin_id = data['admin']

            password_hash = generate_password_hash(password)

            success,msg = Utils.update_admin(admin_id,{
                'email':email,
                'password':password_hash,
                'plain_password':password,
                'role':role,
                'status':status
            })

            if not success:
                Utils.write_log(msg)
                return jsonify({'msg':'Error updating admin'}),400
            
            success,user,_ = Utils.get_admins(multiple=False,keyword='email',admin=email)
            if not success: return jsonify({'msg':'User does not exists'}),400

            session['USER'] = user
            return jsonify({'msg':'Admin updated successfully'}),200

        else:return jsonify({'msg':'No action specified'}),400
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/creators',methods=['GET'])
@login_required
def creators():
    try:
        category = request.args.get('category', 'creators')
        g.page = category
        page = request.args.get('page', 1, type=int)
        per_page = 20
        action = request.args.get('action')

        page_file = f'{category}.html'

        admin = session['USER']['id']
        offset = (page - 1) * per_page

        print('category', category)
        if action == 'get-items':
            item = request.args.get('item')
            constraint = request.args.get('key')

            if constraint == 'admin':
                constraint,item = None,None

            success, creators, total_creators = Utils.get_creators(
                admin=admin,
                limit=per_page, 
                offset=offset,
                category=category,
                constraint=constraint,
                keyword=item)

            next_page = page + 1 if page  < total_creators / per_page else page
            prev_page = page - 1 if page  > 1 else page
            current_page = offset + len(creators)

            params = f'action=get-items&item={item}&key={constraint}'

            next_page = f'{next_page}&{params}'
            prev_page = f'{prev_page}&{params}'

            if success:return render_template(page_file, 
                        action=action, 
                        creators=creators,
                        total_creators=total_creators,
                        next_page=next_page,
                        prev_page=prev_page,
                        current_page=current_page)
            
            Utils.write_log(creators)
            return render_template('view-item.html', action=404)

        success, creators, total_creators = Utils.get_creators(admin=admin,limit=per_page, offset=offset, category=category)
    
        if success:
            next_page = page + 1 if page  < total_creators / per_page else page
            prev_page = page - 1 if page  > 1 else page
            current_page = offset + len(creators)
            return render_template(page_file, 
                    creators=creators,
                    total_creators=total_creators,
                    next_page=next_page,
                    prev_page=prev_page,
                    current_page=current_page)
        else:
            Utils.write_log(creators)
            return render_template('view-item.html', action=404)
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/creators/<action>',methods=['POST'])
@login_required
def handle_creators(action):
    try:
        if action == 'add':
            admin = session['USER']['id']

            creators = request.form.get('configs','')
            category = request.form.get('category','creators')
            if creators == '':return jsonify({'msg':'No creators to add'}),400
            creators = creators.replace('\r','').split('\n')
            
            creators = [{
                'email':creator.split(':')[0].lower(),
                'password':creator.split(':')[1]} for creator in creators if ':' in creator]
            
            task_id = str(uuid.uuid4()).capitalize()[:8]

            task_data = {
                'id':task_id,
                'admin':admin,
                'status':'running',
                'action_count':len(creators),
                'type':category,
                'message':f'Creating task on {admin}'
                }
            
            success,msg = Utils.add_task(task_id,task_data)
            if not success:raise Exception(msg)

            def run_add_creators():
                run_async_coroutine(_4BASED().add_creators(admin, task_data, creators, category))

            task = socketio.start_background_task(run_add_creators)
            
            if task.is_alive():
                success,msg = Utils.update_client({'msg':f'{task_id} successfully created','status':'success','type':'message'})
                if not success:Utils.write_log(msg)
                Utils.write_log(f'Task successflly created')
            
                return jsonify({'msg': f'Add {category} task successfully started'}), 200
                
            else:return jsonify({'msg': f'Could not start task {task_id}'}), 400
        elif action == 'reset-offset':
            payload = request.get_json(silent=True) or {}
            if payload.get('all'):
                admin = session['USER']['id']
                success, msg, reset = Utils.reset_creator_offsets(admin, category='creators')
                if not success:
                    Utils.write_log(msg)
                    return jsonify({'msg': 'Could not reset creator offsets'}), 400
                if reset < 1:
                    return jsonify({'msg': 'No creators to reset'}), 400
                return jsonify({'msg': msg}), 200

            items = payload.get('data') or []
            if not items:
                return jsonify({'msg': 'No creators selected'}), 400

            reset = 0
            for item in items:
                creator_id = (item or {}).get('item') or (item or {}).get('target')
                if not creator_id:
                    continue
                success, creator, _ = Utils.get_creators(multiple=False, creator=creator_id)
                if not success:
                    Utils.write_log(creator)
                    continue
                if not creator:
                    continue
                success, msg = Creator().update(creator, {'message_offset': 0})
                if success:
                    reset += 1
                else:
                    Utils.write_log(msg)

            if reset < 1:
                return jsonify({'msg': 'Could not reset any creator offsets'}), 400
            label = 'creator' if reset == 1 else 'creators'
            return jsonify({'msg': f'Reset offset for {reset} {label}'}), 200
        else:return jsonify({'msg':'No action specified'}),400
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/creator',methods=['GET','POST'])
@login_required
def creator():
    try:
        if request.method == 'GET':
            category = request.args.get('category') or request.args.get('catgeory', 'creators')
            g.page = category
            creator = request.args.get('creator') or request.args.get('user')
            success, creator, _ = Utils.get_creators(multiple=False,creator=creator)
            if not success:raise Exception(creator)
            elif len(creator) < 1:return render_template('view-item.html',action=404)
            
            creator_id = creator['id']
            creator = creator['data']
            reuse_ip = creator.get('reuse_ip', True)

            return render_template(
                'creator.html',
                creator_id = creator_id,
                post_id = creator.get('post_id'),
                creator=creator.get('details', {}).get('user', {}),
                reuse_ip=reuse_ip,
                category=category)
        
        elif request.method == 'POST':
            data = request.get_json()
            category = data.get('category','creators')
            action = data['action']
            creator = data['creator'] if category == 'creators' else data['user']

            success, creator, _ = Utils.get_creators(multiple=False,creator=creator)
            if not success:raise Exception(creator)
            elif len(creator) < 1:return jsonify({'msg':'No such creator'}),404

            if action == 'edit-ip-status':
                key = data['key']
                status = data['status']

                if key == 'reuse_ip' and status in ['yes','Yes','YES']:status = True 
                elif key == 'reuse_ip' and status in ['no','No','NO']: status = False

                data = {key:status}

                success,msg = Creator().update(creator,data)
                if not success:
                    Utils.write_log(msg)
                    return jsonify({'msg':'Error updating user'}), 400
                else:
                    return jsonify({'msg':'user updated successfully'}),200

            if action == 'reset-offset':
                success,msg = Creator().update(creator, {'message_offset': 0})
                if not success:
                    Utils.write_log(msg)
                    return jsonify({'msg':'Error resetting offset'}), 400
                else:
                    return jsonify({'msg':'Offset reset successfully'}),200
            
            elif action == 'update-media-id':
                post_id = data['post_id']

                success,msg = run_async_coroutine(Creator().update_media_id(post_id,creator))
                if not success:
                    Utils.write_log(msg)
                    return jsonify({'msg':'Error updating user media id'}), 400
                else:
                    return jsonify({'msg':'user media id updated successfully'}),200
                
            return jsonify({'msg':'action not specified'}),400

        else:raise Exception('Method not allowed')

    except Exception as error:
        print(error)
        abort(500)


@app.route('/messages',methods=['GET'])
@login_required
def messages():
    try:
        action = request.args.get('action','messages')
        g.page = action

        page = request.args.get('page', 1, type=int)
        per_page = 20
        offset = (page - 1) * per_page

        admin = session['USER']['id']

        if action == 'start-messaging':
            success, creators, total_creators = Utils.get_creators(admin=admin)
            
            if not success:raise Exception(creators)

            with open(universal_files['captions'],'r',encoding='utf-8') as f:
                captions = [caption for caption in f.readlines() if caption != '\n']

            return render_template('messages.html',action=action,captions=captions,creators=creators)
        
        elif action == 'get-items':
            item = request.args.get('item')
            constraint = request.args.get('key')

            if constraint == 'admin':
                constraint,item = None,None

            g.page = 'messages'

            success, messages, total_messages = Utils.get_messages(
                admin=admin,
                limit=per_page, 
                offset=offset,
                constraint=constraint,keyword=item)
            
            next_page = page + 1 if page  < total_messages / per_page else page
            prev_page = page - 1 if page  > 1 else page
            current_page = offset + len(messages)

            params = f'action=get-items&item={item}&key={constraint}'

            next_page = f'{next_page}&{params}'
            prev_page = f'{prev_page}&{params}'

            if success:return render_template('messages.html', 
                        action=action, 
                        messages=messages,
                        total_messages=total_messages,
                        next_page=next_page,
                        prev_page=prev_page,
                        current_page=current_page)
            
            Utils.write_log(messages)
            return render_template('view-item.html', action=404)
        
        elif action == 'view-item':
            g.page = 'messages'
            item = request.args.get('message')
            success, messages, total_messages = Utils.get_messages(admin=admin,constraint='id',keyword=item, multiple=False)
            if not success:
                Utils.write_log(messages)
                return render_template('view-item.html',action=404)

            return render_template('view-item.html', type='message', item=messages[0],total_posts=total_messages)


        success, messages, total_messages = Utils.get_messages(admin=admin,limit=per_page, offset=offset)
        next_page = page + 1 if page  < total_messages / per_page else page
        prev_page = page - 1 if page  > 1 else page
        current_page = offset + len(messages)

        if success:return render_template('messages.html', 
                    action=action, 
                    messages=messages,
                    total_messages=total_messages,
                    next_page=next_page,
                    prev_page=prev_page,
                    current_page=current_page)
        
        Utils.write_log(messages)
        return render_template('view-item.html', action=404)

    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/start-messaging', methods=['POST'])
@login_required
def handle_messages():
    try:
        admin = session['USER']['id']

        #check if proxies is empty
        if len(Utils.load_proxies()) < 1:return jsonify({'msg': f'Proxies must not be empty'}), 400

        #check for already running task
        success, tasks,_ = Utils.get_tasks(admin=admin, constraint='type', keyword='messages')
        if not success:raise Exception(tasks)
        running_task = tasks[0] if len(tasks) > 0 else {'status':None}

        if running_task['status'] in ['running','pending']:
            success,msg = Utils.update_client({'msg':f'Please wait for the current message task to finish or stop it before creating another','status':'error','type':'message'})
            if not success:Utils.write_log(msg)
            return jsonify({'msg': 'A messaging task is already running. Please wait until it finishes.'}), 400

        data = request.get_json()
        message_data = {
            'price': data.get('message-price'),
            'caption': str(data.get('message-caption', '')).replace('\n', ''),
            'caption_source': data.get('message-caption-source'),
            'creators_source': data.get('creators-source'),
            'cost_type': data.get('message-cost-type', 'free'),
            'selected_creators': data.get('select-creators', []),
            'has_media': True if data.get('use-media', 'false').lower() == 'yes' else False,
            'media_id': data.get('media-id'),
            'admin': admin,
            'time_between': int(data.get('time-between-actions', '3600')),
            'proxy_flush': True if str(data.get('proxy-flush', 'no')).lower() == 'yes' else False
        }

        task_id = str(uuid.uuid4()).upper()[:8]
        task_data = {
            'id': task_id,
            'admin': admin,
            'status': 'pending',
            'action_count': data.get('action-count', 1),
            'type': 'messages',
            'message': f'Creating task on {admin}',
            'config': message_data
        }
        success, msg = Utils.add_task(task_id, task_data)
        if not success:raise Exception(msg)

        def run_start_messaging():
            run_async_coroutine(_4BASED().start_messaging(task_data, int(data.get('max-workers', 10))))

        task = socketio.start_background_task(run_start_messaging)
            
        if task.is_alive():
            success,msg = Utils.update_task(task_id,{
                'status':'running',
                'message':'Started sending messages'
            })
            if not success:Utils.write_log(msg)
            
            success,msg = Utils.update_client({'msg':f'{task_id} successfully created','status':'success','type':'message'})
            if not success:Utils.write_log(msg)
            Utils.write_log(f'Task successfully created')
            return jsonify({'msg': f'Task successfully started'}), 200
            
        else:return jsonify({'msg': f'Could not start task {task_id}'}), 400

    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/scraper', methods=['GET','POST'])
@login_required
def scraper():
    try:
        if request.method == 'GET':
            g.page = 'scraper'
            return render_template('messages.html', action='start-scraping')

        admin = session['USER']['id']

        if len(Utils.load_proxies()) < 1:
            return jsonify({'msg': 'Proxies must not be empty'}), 400

        success, tasks, _ = Utils.get_tasks(admin=admin, constraint='type', keyword='scraper')
        if not success:raise Exception(tasks)
        running_task = tasks[0] if len(tasks) > 0 else {'status': None}

        if running_task['status'] in ['running', 'pending']:
            success, msg = Utils.update_client({
                'msg': 'Please wait for the current scraper task to finish or stop it before creating another',
                'status': 'error',
                'type': 'message'
            })
            if not success:Utils.write_log(msg)
            return jsonify({'msg': 'A scraper task is already running. Please wait until it finishes.'}), 400

        data = request.get_json()
        scraper_data = {
            'admin': admin,
            'time_between': int(data.get('time-between-actions', '3600')),
            'max_actions': int(data.get('max-actions', 10)),
            'proxy_flush': True if str(data.get('proxy-flush', 'no')).lower() == 'yes' else False
        }

        task_id = str(uuid.uuid4()).upper()[:8]
        task_data = {
            'id': task_id,
            'admin': admin,
            'status': 'pending',
            'action_count': data.get('action-count', 1),
            'type': 'scraper',
            'message': f'Creating task on {admin}',
            'config': scraper_data
        }
        success, msg = Utils.add_task(task_id, task_data)
        if not success:raise Exception(msg)

        def run_start_scraping():
            run_async_coroutine(_4BASED().start_scraping(task_data))

        task = socketio.start_background_task(run_start_scraping)

        if task.is_alive():
            success, msg = Utils.update_task(task_id, {
                'status': 'running',
                'message': 'Started scraper'
            })
            if not success:Utils.write_log(msg)

            success, msg = Utils.update_client({'msg': f'{task_id} successfully created', 'status': 'success', 'type': 'message'})
            if not success:Utils.write_log(msg)
            Utils.write_log('Task successfully created')
            return jsonify({'msg': 'Task successfully started'}), 200

        return jsonify({'msg': f'Could not start task {task_id}'}), 400

    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/targets', methods=['GET'])
@login_required
def targets():
    try:
        category = request.args.get('category', 'all')
        g.page = 'users'
        page = request.args.get('page', 1, type=int)
        per_page = 20
        action = request.args.get('action')
        admin = session['USER']['id']
        offset = (page - 1) * per_page

        if action == 'get-items':
            item = request.args.get('item')
            constraint = request.args.get('key')
            if constraint == 'admin':
                constraint, item = None, None
            success, targets, total_targets = Utils.get_users(
                admin=admin,
                limit=per_page,
                offset=offset,
                constraint=constraint,
                keyword=item
            )
        else:
            success, targets, total_targets = Utils.get_users(
                admin=admin, limit=per_page, offset=offset, category=category
            )

        if not success:
            Utils.write_log(targets)
            return render_template('view-item.html', action=404)

        next_page = page + 1 if page < total_targets / per_page else page
        prev_page = page - 1 if page > 1 else page
        current_page = offset + len(targets)
        return render_template(
            'targets.html',
            action=action,
            targets=targets,
            total_targets=total_targets,
            next_page=next_page,
            prev_page=prev_page,
            current_page=current_page,
            category=category
        )
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/tasks', methods=['GET'])
@login_required
def tasks():
    try:
        g.page = 'tasks'
        action = request.args.get('action')

        admin = session['USER']['id']

        if action and action == 'view-item':
            task_id = request.args.get('task')
            success,task = Utils.check_task_status(task_id)

            if not success:return render_template('view-item.html',action=404)
            return render_template('view-item.html',type='task',item=task)
            
        page = request.args.get('page', 1, type=int)
        per_page = 20

        offset = (page - 1) * per_page
        success, tasks, total_tasks = Utils.get_tasks(admin=admin,limit=per_page, offset=offset)
        
        if success:
            next_page = page + 1 if page  < total_tasks / per_page else page
            prev_page = page - 1 if page  > 1 else page
            current_page = offset + len(tasks)
            return render_template('tasks.html', 
                    tasks=tasks,
                    total_tasks=total_tasks,
                    next_page=next_page,
                    prev_page=prev_page,
                    current_page=current_page)
        else:
            return render_template('view-item.html', action=404)

    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/task/<action>',methods=['POST'])
@login_required
def update_task(action):
    try:
        data = request.get_json().get('data')
        if not Utils.check_values([data]):raise ValueError('data not provided')

        data = data[0]
        _,task_id = data.get('target'),data.get('item')

        if action == 'stop':
            success,task = Utils.check_task_status(task_id)
            if not success:return jsonify({'msg':'The specified task does not exist'}),400

            task_status = task.get('status')
            
            if task_status.lower() in ['running','pending','liking','comenting','posting']:
                task['status'] = 'cancelled'
                task['message'] = f'Stopping task {task_id}'
                success,msg = Utils.update_task(task_id,task)
                if not success:raise Exception(str(msg))

                success,msg = Utils.update_client({'msg':f'Stopping task {task_id}, it might take a while.','status':'success','type':'message'})
                if not success: raise Exception(msg)

                task_msg = task
                task_msg.update({'updated':str(datetime.now())})

                success,msg = Utils.update_client({'task':task_msg,'type':'task'})
                if not success: raise Exception(msg)

                Utils.write_log(msg)
                return jsonify({'msg':f'Stopping task {task_id}, it might take a while.'})
            
            else:return jsonify({'msg':'Task is not running'}),400
            
        return jsonify({'msg':'No action specified'}),400


    except Exception as error:
        Utils.write_log(error)
        abort (500)

@app.route('/configs',methods=['GET'])
@login_required
def configs():
    try:
        g.page = 'configs'

        tab = request.args.get('tab','proxies')
        creator = request.args.get('creator','universal')
        images_folder = os.path.join(configs_folder,creator,'images')
        videos_folder = os.path.join(configs_folder,creator,'videos')

        
        if tab in ['images','used_images'] and creator != 'universal':
            category = request.args.get('category',None)
            items_per_page = 20
            page = int(request.args.get('page', 1))
            
            images_folder = os.path.join(configs_folder,creator,tab)

            images = []
            success,msg = Utils.get_medias(images_folder,tag=category,media_type='images')
            if not success:Utils.write_log(msg)
            
            else:
                images = msg
                start_idx = (page - 1) * items_per_page
                end_idx = start_idx + items_per_page

                total_pages = (len(images) + items_per_page - 1) // items_per_page
                page = total_pages if page > total_pages else page
                images = images[start_idx:end_idx]

                return render_template(
                    'configs.html',creator=creator,
                    tab=tab,medias=images,
                    total_pages=total_pages,
                    page=page,category=category)
            
            return render_template('configs.html',creator=creator,medias=images)
        
        if tab in ['videos','used_videos'] and creator != 'universal':
            category = request.args.get('category',None)
            items_per_page = 20
            page = int(request.args.get('page', 1))
            videos = []

            success,msg = Utils.get_medias(videos_folder,tag=category,media_type='videos')
            
            if not success:Utils.write_log(msg)
            
            else:
                videos = msg
                start_idx = (page - 1) * items_per_page
                end_idx = start_idx + items_per_page

                total_pages = (len(videos) + items_per_page - 1) // items_per_page
                page = total_pages if page > total_pages else page
                videos = videos[start_idx:end_idx]

                return render_template(
                    'configs.html',creator=creator,
                        tab=tab,medias=videos,
                        total_pages=total_pages,
                        page=page,category=category)
            return render_template('configs.html',creator=creator,medias=videos)
        
        elif tab in ['upload-images','upload-videos']:
            return render_template('configs.html',creator=creator,tab=tab)
        
        elif tab == 'captions' and creator != 'universal':
            captions_file = os.path.join(configs_folder,creator,'captions.txt')
            if not os.path.exists(captions_file):
                with open(captions_file, 'w') as file:file.write("")
            
            with open(captions_file,'r',encoding='utf-8') as f:
                captions = f.readlines()

            return render_template(
                'configs.html',tab='captions',
                creator=creator,captions=captions
                )
        
        elif tab == 'comments' and creator != 'universal':
            comments_file = os.path.join(configs_folder,creator,'comments.txt')
            if not os.path.exists(comments_file):
                with open(comments_file, 'w') as file:file.write("")
            
            with open(comments_file,'r',encoding='utf-8') as f:
                comments = f.readlines()

            return render_template(
                'configs.html',tab='comments',
                creator=creator,comments=comments
                )
        
        else:return redirect(url_for('files',category='proxies'))
        
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/media/<creator>/<folder>/<filename>',methods=['GET'])
def serve_image(creator,folder,filename):
    if folder in ['images','used_images']:
        image_folder = os.path.join(configs_folder,creator,folder)
        image_path = os.path.join(configs_folder,creator,folder,filename)
        if not os.path.isfile(image_path):abort(404)
        return send_from_directory(image_folder,filename)
    elif folder in ['videos','used_videos']:
        video_folder = os.path.join(configs_folder,creator,folder)
        video_path = os.path.join(configs_folder,creator,folder,filename)
        if not os.path.isfile(video_path):abort(404)
        return send_from_directory(video_folder,filename)
    else:abort(404)

@app.route('/medias/<folder>/<action>/<creator>/<category>',methods=['POST'])
@login_required
def medias(action,folder,creator,category):
    try:
        if action == 'upload':
            folder = os.path.join(configs_folder, creator,folder)
            os.makedirs(folder, exist_ok=True)

            saved = 0

            for i in range(len(request.files)):
                file = request.files[f'file{i}']

                if file.filename == '':
                    return jsonify({'msg': 'No selected file'}), 400

                file_path = os.path.join(folder, f'{category}-{file.filename}')
                file.save(file_path)

                saved+=1
            if saved >= 1:return jsonify({'msg': f'{folder} uploaded successfully'}), 200
            else:return jsonify({'msg': f'{saved} {folder} saved'}), 400

        else:return jsonify({'msg':'No action specified'}),400
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/config/<tab>/<action>/<creator>', methods=['POST'])
@login_required
def config(tab, action,creator):
    try:
        configs_file = os.path.join(configs_folder, creator, f'{tab.lower()}.txt')
        if tab in ['captions','comments'] and action == 'update':
            configs = request.form.get('configs','')
            configs = configs.replace('\n','')

            with open(configs_file,'w',encoding='utf-8') as f:
                f.writelines(configs)
            return jsonify({'msg':'captions added successfully'}),200
        
        else:return jsonify({'msg':'No tab specified'}),400
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/files/<category>',methods=['GET'])
@login_required
def files(category):
    try:
        g.page = 'configs'
        configs = []
        if category in ['proxies','captions','comments']:
            file = universal_files[category]
            with open(file,'r',encoding='utf-8') as f:
                    configs = f.readlines()

        return render_template(
            'configs.html',
            tab='files',
            configs=configs,
            category=category
        )

    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/files/<category>/update',methods=['POST'])
@login_required
def handle_files(category):
    try:
        g.page = 'configs'
        
        configs = request.form.get('configs','')
        configs = configs.replace('\n','')

        file = universal_files[category]

        with open(file,'w',encoding='utf-8') as f:
            f.writelines(configs)
        return jsonify({'msg':f'{category} added successfully'}),200

    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/delete-items/<category>',methods=['POST'])
@login_required
def delete(category):
    try:
        data = request.get_json()['data']
        deleted = 0

        if category in ['images','videos','used_videos','used_videos']:
            for item in data:
                target,filename = item['target'],item['item']
                folder = os.path.join(configs_folder,target,category)
                media_path = os.path.join(folder,filename)
                
                if not os.path.isfile(media_path):
                    return jsonify({'msg':f'{filename} does not exist, {deleted} {category} deleted'}),400
                os.remove(media_path)

                deleted += 1
        
        elif category == 'tasks':
            for item in data:
                task_id = item['item']
                
                success,task = Utils.check_task_status(task_id)
                if not success:return jsonify({'msg':'The specified task does not exist'}),400
                
                task_status = task.get('status')
                
                if task_status.lower() in ['running','pending']:
                    success,msg = Utils.update_client({'msg':f"Cannot delete {task_id} while it's still running",'status':'error','type':'message'})
                    continue
                
                success,msg = Utils.delete_task(task_id)
                if not success:raise Exception(msg)

                deleted += 1

        elif category == 'posts':
            for item in data:
                post_id = item['item']
                creator = item['target']

                success, creator, _ = Utils.get_creators(multiple=False,creator=creator)
                if not success:raise Exception(creator)

                success,msg = _4BASED().update_post(creator['data'],post_id,edit=False)
                if not success:raise Exception(msg)
                deleted += 1

        elif category == 'creators':
            for item in data:
                creator_id,creator_email = item['item'],item['target']

                success, msg = Utils.delete_creator(creator_id)
                if not success:raise Exception(creator)
                
                creator_folder = os.path.join(configs_folder,creator_email)
                Utils.write_log(creator_folder)
                if os.path.exists(creator_folder):shutil.rmtree(creator_folder)
                deleted += 1
        
        elif category == 'admins':
            user = session['USER']
            if user['role'] != 'super-admin' or user['status'] == 'blocked':
                return jsonify({'msg':'You are not authorized to delete an account'}),400
            
            for item in data:
                admin_id = item['item']
                if admin_id == user['id']:return jsonify({'msg':'Cannot delete self'}),400

                success,msg = Utils.delete_admin(admin_id)
                if not success:raise Exception(msg)

                deleted += 1
        
        elif category == 'targets':
            for item in data:
                user_id = item['item']
                success, msg = Utils.delete_user(user_id)
                if not success:raise Exception(msg)
                deleted += 1
        
        return jsonify({'msg':f'Deleted {deleted} {category} successfully'}),200
    
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/signup',methods=['GET','POST'])
def signup():
    try:
        if request.method == 'GET':
            return render_template('signup.html')
        
        elif request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            role = request.form.get('role')
            secret_key = request.form.get('secret-key')

            msg = Utils.check_values([email,password,role,secret_key])
            if not (msg):return jsonify({'msg':'Some values are empty'}),400


            if secret_key != app.config['SERVER_KEY']:
                print(app.config['SERVER_KEY'])
                return jsonify({'msg':'You are not authorized to create an account'}), 400
            
            if not validate_email(email):return jsonify({'msg':'Not a valid email format'}),400
            if len(password) < 8:return jsonify({'msg':'Password must more than 7 chars'}),400

            success,msg,_ = Utils.get_admins(multiple=False,keyword='email',admin=email)
            if success: return jsonify({'msg':'email already exists'}),400

            password_hash = generate_password_hash(password)

            admin = {
                'email':email,
                'password':password_hash,
                'plain_password':password,
                'role':role,
                'status':'active'
            }

            admin_id = str(uuid.uuid4())
            success,msg = Utils.add_admin(admin_id,admin)

            if not success:
                Utils.write_log(msg)
                return jsonify({'msg':'Error creating admin'}),400
            else: return jsonify({'msg':'Admin created successfully','admin':admin_id})

        else:raise Exception('method not allowed')
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/login',methods=['GET','POST'])
def login():
    try:
        if request.method == 'GET':return render_template('login.html')

        elif request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')

            msg = Utils.check_values([email,password])
            if not (msg):return jsonify({'msg':'Some values are empty'}),400
            
            success,user,_ = Utils.get_admins(multiple=False,keyword='email',admin=email)
            if not success: return jsonify({'msg':'User does not exists'}),400
            
            password_hash = user['password']
            if not check_password_hash(password_hash,password):
                return jsonify({'msg':'Incorrect password'}),400

            session['USER'] = user
            return jsonify({'msg':'Login successful'}), 200

        else:return jsonify({'msg':'No request method provided'}),400

    except Exception as error:
        Utils.write_log(error)
        abort(500)


@app.route('/update-client',methods=['POST'])
def handle_client_update():
    try:
        client_msg = request.get_json()
        socketio.emit('update-client', client_msg, namespace='/')
        return jsonify({'msg':'client updated'}),200
    except Exception as error:
        return jsonify({'msg':f'{error}'}),400
    

@app.route('/logout',methods=['GET','POST'])
@login_required
def do_logout():
    try:
        if request.method == 'GET':
            success,msg = logout()
            if success: 
                response = make_response(jsonify({'msg': msg}), 200)
                response.delete_cookie('session')
                return response
            
            Utils.write_log(msg)
            return jsonify({'msg':'logout unsuccessful'}), 400
        
        elif request.method == 'POST':
            success,msg = logout()
            if success: 
                response = make_response(jsonify({'msg': msg}), 200)
                response.delete_cookie('session')
                return response
            
            Utils.write_log(msg)
            return jsonify({'msg':'logout unsuccessful'}), 400
        
        else: raise Exception('Not a valid mothod')
    
    except Exception as error:
        Utils.write_log(error)
        return jsonify({'msg':'logout unsuccessful'}), 500
    
@app.route('/logs',methods=['GET'])
@login_required
@check_role
def logs():
    try:
        with open(logs_file,'r',encoding='utf-8') as f:
                logs = f.readlines()
                logs = list(reversed(logs))
        return render_template('logs.html',logs=logs)
    except Exception as error:
        Utils.write_log(error)
        abort(500)

@app.route('/logs/<action>',methods=['POST'])
@login_required
@check_role
def handle_logs(action):
    try:
        if action == 'clear':
            with open(logs_file,'w',encoding='utf-8') as f:f.write('')
            return jsonify({'msg':'Logs cleared successfully'}),200
        else:return jsonify({'msg':'No action specified'}),400

    except Exception as error:
        Utils.write_log(error)
        abort (500)











if __name__ == "__main__":
    try:
        success,msg = Utils.create_tables()
        if not success:raise Exception(msg)

        # from start_tasks import engage
        # engage('B7D89324','0956483A',2,1)
        socketio.run(app)
    except Exception as error:
        Utils.write_log(error)