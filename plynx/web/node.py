from __future__ import absolute_import
import json
from flask import request, g
from plynx.db.node import Node
from plynx.db.node_collection_manager import NodeCollectionManager
from plynx.plugins.managers import resource_manager, executor_manager
from plynx.web.common import app, requires_auth, make_fail_response, handle_errors
from plynx.utils.common import to_object_id, JSONEncoder
from plynx.constants import NodeStatus, NodeRunningStatus, NodePostAction, NodePostStatus, Collections

PAGINATION_QUERY_KEYS = {'per_page', 'offset', 'status', 'base_node_names', 'search', 'is_graph'}
PERMITTED_READONLY_POST_ACTIONS = {
    NodePostAction.VALIDATE,
    NodePostAction.PREVIEW_CMD,
}

node_collection_managers = {
    collection: NodeCollectionManager(collection=collection)
    for collection in [Collections.NODES, Collections.RUNS]
}

@app.route('/plynx/api/v0/search_<collection>', methods=['POST'])
@handle_errors
@requires_auth
def post_search_nodes(collection):
    query = json.loads(request.data)
    app.logger.debug(request.data)

    user_id = to_object_id(g.user._id)
    if len(query.keys() - PAGINATION_QUERY_KEYS):
        return make_fail_response('Unknown keys: `{}`'.format(query.keys() - PAGINATION_QUERY_KEYS)), 400

    app.logger.debug(query)
    res = node_collection_managers[collection].get_db_nodes(user_id=user_id, **query)

    return JSONEncoder().encode({
        'items': res['list'],
        'total_count': res['metadata'][0]['total'] if res['metadata'] else 0,
        'plugins_dict': {
            'resources_dict': resource_manager.resources_dict,
            'executors_info': executor_manager.executors_info,
        },
        'status': 'success'})


@app.route('/plynx/api/v0/<collection>/<node_link>', methods=['GET'])
@handle_errors
@requires_auth
def get_nodes(collection, node_link=None):
    user_id = to_object_id(g.user._id)
    # if node_link is a base node
    if node_link in executor_manager.executors_info:
        data = executor_manager.name_to_class[node_link].get_default_node().to_dict()
        data['kind'] = node_link
        return JSONEncoder().encode({
            'data': data,
            'plugins_dict': {
                'resources_dict': resource_manager.resources_dict,
                'executors_info': executor_manager.executors_info,
            },
            'status': 'success'})
    else:
        try:
            node_id = to_object_id(node_link)
        except Exception:
            return make_fail_response('Invalid ID'), 404
        node = node_collection_managers[collection].get_db_node(node_id, user_id)
        app.logger.debug(node)
        if node:
            return JSONEncoder().encode({
                'data': node,
                'plugins_dict': {
                    'resources_dict': resource_manager.resources_dict,
                    'executors_info': executor_manager.executors_info,
                },
                'status': 'success'})
        else:
            return make_fail_response('Node `{}` was not found'.format(node_link)), 404


@app.route('/plynx/api/v0/<collections>', methods=['POST'])
@handle_errors
@requires_auth
def post_node(collections):
    app.logger.debug(request.data)

    data = json.loads(request.data)

    node = Node.from_dict(data['node'])
    node.author = g.user._id
    node.starred = False
    db_node = node_collection_managers[collections].get_db_node(node._id, g.user._id)
    action = data['action']
    if db_node and db_node['_readonly'] and action not in PERMITTED_READONLY_POST_ACTIONS:
        return make_fail_response('Permission denied'), 403

    if action == NodePostAction.SAVE:
        if node.node_status != NodeStatus.CREATED and node.base_node_name != 'file':
            return make_fail_response('Cannot save node with status `{}`'.format(node.node_status))

        node.save(force=True)

    elif action == NodePostAction.APPROVE:
        if node.node_status != NodeStatus.CREATED:
            return make_fail_response('Node status `{}` expected. Found `{}`'.format(NodeStatus.CREATED, node.node_status))
        validation_error = node.get_validation_error()
        if validation_error:
            return JSONEncoder().encode({
                'status': NodePostStatus.VALIDATION_FAILED,
                'message': 'Node validation failed',
                'validation_error': validation_error.to_dict()
            })

        node.node_status = NodeStatus.READY
        node.save(force=True)

    elif action == NodePostAction.CREATE_RUN:
        if node.node_status != NodeStatus.CREATED:
            return make_fail_response('Node status `{}` expected. Found `{}`'.format(NodeStatus.CREATED, node.node_status))
        validation_error = node.get_validation_error()
        if validation_error:
            return JSONEncoder().encode({
                'status': NodePostStatus.VALIDATION_FAILED,
                'message': 'Node validation failed',
                'validation_error': validation_error.to_dict()
            })

        node = node.clone()
        node.save(collection=Collections.RUNS)
        return JSONEncoder().encode(
            {
                'status': NodePostStatus.SUCCESS,
                'message': 'Run Node(_id=`{}`) successfully created'.format(str(node._id)),
                'run_id': str(node._id),
            })


    elif action == NodePostAction.VALIDATE:
        validation_error = node.get_validation_error()

        if validation_error:
            return JSONEncoder().encode({
                'status': NodePostStatus.VALIDATION_FAILED,
                'message': 'Node validation failed',
                'validation_error': validation_error.to_dict()
            })
    elif action == NodePostAction.DEPRECATE:
        if node.node_status == NodeStatus.CREATED:
            return make_fail_response('Node status `{}` not expected.'.format(node.node_status))

        node.node_status = NodeStatus.DEPRECATED
        node.save(force=True)
    elif action == NodePostAction.MANDATORY_DEPRECATE:
        if node.node_status == NodeStatus.CREATED:
            return make_fail_response('Node status `{}` not expected.'.format(node.node_status))

        node.node_status = NodeStatus.MANDATORY_DEPRECATED
        node.save(force=True)
    elif action == NodePostAction.PREVIEW_CMD:
        job = node_collection.make_job(node)

        return JSONEncoder().encode(
            {
                'status': NodePostStatus.SUCCESS,
                'message': 'Successfully created preview',
                'preview_text': job.run(preview=True)
            })

    else:
        return make_fail_response('Unknown action `{}`'.format(action))

    return JSONEncoder().encode(
        {
            'status': NodePostStatus.SUCCESS,
            'message': 'Node(_id=`{}`) successfully updated'.format(str(node._id))
        })
