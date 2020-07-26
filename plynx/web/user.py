from flask import g, request, jsonify
from json import loads
import plynx.db.node_collection_manager
from plynx.db.db_object import get_class
from plynx.db.demo_user_manager import DemoUserManager
from plynx.db.user import UserCollectionManager
from plynx.web.common import app, requires_auth, make_fail_response, handle_errors
from plynx.utils.common import JSONEncoder, to_object_id
from plynx.constants import Collections, NodeClonePolicy
from plynx.utils.db_connector import get_db_connector
from plynx.utils.config import get_settings_config, get_auth_config
from plynx.db.user import User
from itsdangerous import JSONWebSignatureSerializer as JSONserializer


demo_user_manager = DemoUserManager()
template_collection_manager = plynx.db.node_collection_manager.NodeCollectionManager(collection=Collections.TEMPLATES)

@app.route('/plynx/api/v0/token', strict_slashes=False)
@requires_auth
@handle_errors
def get_auth_token():
    access_token = g.user.generate_access_token()
    refresh_token = g.user.generate_refresh_token()

    user_obj = g.user.to_dict()
    user_obj['hash_password'] = ''
    return JSONEncoder().encode({
        'access_token': access_token.decode('ascii'),
        'refresh_token': refresh_token.decode('ascii'),
        'user': user_obj,
    })


@app.route('/plynx/api/v0/demo', methods=['POST'])
@handle_errors
def post_demo_user():
    user = demo_user_manager.create_demo_user()
    if not user:
        return make_fail_response('Failed to create a demo user')

    template_id = DemoUserManager.demo_config.kind
    if DemoUserManager.demo_config.template_id:
        try:
            node_id = to_object_id(DemoUserManager.demo_config.template_id)
        except Exception as e:
            app.logger.error('node_id `{}` is invalid'.format(DemoUserManager.demo_config.template_id))
            app.logger.error(e)
            return make_fail_response('Failed to create a demo node')
        try:
            user_id = user._id
            node = template_collection_manager.get_db_node(node_id, user_id)
            node = get_class(node['_type'])(node).clone(NodeClonePolicy.NODE_TO_NODE)
            node.author = user_id
            node.save()
            template_id = node._id
        except Exception as e:
            app.logger.error('Failed to create a demo node')
            app.logger.error(e)
            return make_fail_response(str(e)), 500

    access_token = user.generate_access_token(expiration=1800)
    user_obj = g.user.to_dict()
    user_obj['hash_password'] = ''
    return JSONEncoder().encode({
        'access_token': access_token.decode('ascii'),
        'refresh_token': 'Not assigned',
        'user': user_obj,
        'url': '/{}/{}'.format(Collections.TEMPLATES, template_id),
    })


@app.route('/plynx/api/v0/users/<username>', methods=['GET'])
@handle_errors
@requires_auth
def get_user(username):
    user = UserCollectionManager.find_user_by_name(username)
    if not user:
        return make_fail_response('User not found'), 404
    user_obj = user.to_dict()
    return JSONEncoder().encode({
        'user': user_obj,
        'status': 'success',
    })
