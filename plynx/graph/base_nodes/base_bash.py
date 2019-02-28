from subprocess import Popen
import os
import stat
import shutil
import signal
import uuid
import logging
import pwd
import json
import zipfile
from plynx.constants import JobReturnStatus, NodeStatus, FileTypes, ParameterTypes
from plynx.db import Node, Output, Parameter
from plynx.utils.common import zipdir
from plynx.utils.file_handler import get_file_stream, upload_file_stream
from plynx.utils.config import get_worker_config, get_cloud_service_config
from plynx.graph.base_nodes import BaseNode

WORKER_CONFIG = get_worker_config()
CLOUD_SERVICE_CONFIG = get_cloud_service_config()
TMP_DIR = '/tmp'


class BaseBash(BaseNode):
    def __init__(self, node=None):
        super(BaseBash, self).__init__(node)
        self.sp = None
        self.base_workdir = str(uuid.uuid1())
        self.workdir = os.path.join(TMP_DIR, self.base_workdir)

    def exec_script(self, script_location, logs, command='bash'):
        res = JobReturnStatus.SUCCESS

        try:
            pw_record = None
            if WORKER_CONFIG.user:
                pw_record = pwd.getpwnam(WORKER_CONFIG.user)

            def pre_exec():
                if WORKER_CONFIG.user:
                    user_uid = pw_record.pw_uid
                    user_gid = pw_record.pw_gid
                    os.setgid(user_gid)
                    os.setuid(user_uid)
                # Restore default signal disposition and invoke setsid
                for sig in ('SIGPIPE', 'SIGXFZ', 'SIGXFSZ'):
                    if hasattr(signal, sig):
                        signal.signal(getattr(signal, sig), signal.SIG_DFL)
                os.setsid()

            env = os.environ.copy()
            shutil.copyfile(script_location, logs['worker'])
            with open(logs['stdout'], 'wb') as stdout_file, open(logs['stderr'], 'wb') as stderr_file:
                self.sp = Popen(
                    [command, script_location],
                    stdout=stdout_file, stderr=stderr_file,
                    cwd=self.workdir, env=env,
                    preexec_fn=pre_exec)

                self.sp.wait()

            if self.sp.returncode:
                raise Exception("Process returned non-zero value")

        except Exception as e:
            res = JobReturnStatus.FAILED
            logging.exception("Job failed")
            with open(logs['worker'], 'a+') as worker_log_file:
                worker_log_file.write('\n' * 3)
                worker_log_file.write('#' * 60 + '\n')
                worker_log_file.write('JOB FAILED\n')
                worker_log_file.write('#' * 60 + '\n')
                worker_log_file.write(str(e))

        return res

    def kill_process(self):
        if hasattr(self, 'sp') and self.sp:
            logging.info('Sending SIGTERM signal to bash process group')
            try:
                os.killpg(os.getpgid(self.sp.pid), signal.SIGTERM)
            except OSError as e:
                logging.error('Error: {}'.format(e))

    def init_workdir(self):
        if not os.path.exists(self.workdir):
            os.makedirs(self.workdir)

    def clean_up(self):
        if os.path.exists(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)

    # Hack: do not pickle file
    def __getstate__(self):
        d = dict(self.__dict__)
        if 'sp' in d:
            del d['sp']
        return d

    @classmethod
    def get_default(cls):
        node = Node()
        node.title = ''
        node.description = ''
        node.base_node_name = cls.get_base_name()
        node.node_status = NodeStatus.CREATED
        node.public = False
        node.parameters = [
            Parameter.from_dict({
                'name': 'cmd',
                'parameter_type': ParameterTypes.TEXT,
                'value': 'bash -c " "',
                'mutable_type': False,
                'publicable': False,
                'removable': False,
                }
            ),
            Parameter.from_dict({
                'name': 'cacheable',
                'parameter_type': ParameterTypes.BOOL,
                'value': True,
                'mutable_type': False,
                'publicable': False,
                'removable': False,
            })
        ]
        node.logs = [
            Output.from_dict({
                'name': 'stderr',
                'file_type': FileTypes.FILE,
                'resource_id': None,
            }),
            Output({
                'name': 'stdout',
                'file_type': FileTypes.FILE,
                'resource_id': None,
            }),
            Output({
                'name': 'worker',
                'file_type': FileTypes.FILE,
                'resource_id': None,
            }),
        ]
        return node

    def _prepare_inputs(self, preview=False, pythonize=False):
        res_inputs, res_cloud_inputs = {}, {}
        for input in self.node.inputs:
            filenames, cloud_filenames = [], []
            if preview:
                for i, value in enumerate(range(input.min_count)):
                    if FileTypes.CLOUD_STORAGE in input.file_types:
                        cloud_filename = os.path.join(
                            '{prefix}/{workdir}/i_{index}_{name}'.format(
                                prefix=CLOUD_SERVICE_CONFIG.prefix,
                                workdir=self.base_workdir,
                                index=i,
                                name=input.name,
                            )
                        )
                        cloud_filenames.append(cloud_filename)
                    filename = os.path.join(self.workdir, 'i_{}_{}'.format(i, input.name))
                    filenames.append(filename)
            else:
                for i, value in enumerate(input.values):
                    filename = os.path.join(self.workdir, 'i_{}_{}'.format(i, input.name))
                    with open(filename, 'wb') as f:
                        f.write(get_file_stream(value.resource_id).read())
                    if FileTypes.EXECUTABLE in input.file_types:
                        # `chmod +x` to the executable file
                        st = os.stat(filename)
                        os.chmod(filename, st.st_mode | stat.S_IEXEC)
                    elif FileTypes.DIRECTORY in input.file_types:
                        # extract zip file
                        zip_filename = '{}.zip'.format(filename)
                        os.rename(filename, zip_filename)
                        os.mkdir(filename)
                        with zipfile.ZipFile(zip_filename) as zf:
                            zf.extractall(filename)
                    elif FileTypes.CLOUD_STORAGE in input.file_types:
                        with open(filename) as f:
                            cloud_filename = json.load(f)['path']
                        cloud_filenames.append(cloud_filename)
                    filenames.append(filename)
            if pythonize:
                if input.min_count == 1 and input.max_count == 1:
                    res_inputs[input.name] = filenames[0]
                    if FileTypes.CLOUD_STORAGE in input.file_types:
                        res_cloud_inputs[input.name] = cloud_filenames[0]
                else:
                    res_inputs[input.name] = filenames
                    if FileTypes.CLOUD_STORAGE in input.file_types:
                        res_cloud_inputs[input.name] = cloud_filenames
            else:
                # TODO is ' ' standard separator?
                res_inputs[input.name] = ' '.join(filenames)
                if FileTypes.CLOUD_STORAGE in input.file_types:
                    res_cloud_inputs[input.name] = ' '.join(cloud_filenames)
        return res_inputs, res_cloud_inputs

    def _prepare_outputs(self, preview=False):
        res_outputs, res_cloud_outputs = {}, {}
        for output in self.node.outputs:
            if preview:
                if output.file_type == FileTypes.CLOUD_STORAGE:
                    res_cloud_outputs[output.name] = os.path.join(
                        '{prefix}/{workdir}/o_{name}'.format(
                            prefix=CLOUD_SERVICE_CONFIG.prefix,
                            workdir=self.base_workdir,
                            name=output.name,
                        )
                    )
                filename = os.path.join(self.workdir, 'o_{}'.format(output.name))
            else:
                filename = os.path.join(self.workdir, 'o_{}'.format(output.name))
                if output.file_type == FileTypes.DIRECTORY:
                    os.mkdir(filename)
                elif FileTypes.CLOUD_STORAGE == output.file_type:
                    cloud_filename = os.path.join(
                        '{prefix}/{workdir}/o_{name}'.format(
                            prefix=CLOUD_SERVICE_CONFIG.prefix,
                            workdir=self.base_workdir,
                            name=output.name,
                        )
                    )
                    with open(filename, 'w') as f:
                        json.dump({"path": cloud_filename}, f)
                    res_cloud_outputs[output.name] = cloud_filename
            res_outputs[output.name] = filename
        return res_outputs, res_cloud_outputs

    def _prepare_logs(self):
        res = {}
        for log in self.node.logs:
            filename = os.path.join(self.workdir, 'l_{}'.format(log.name))
            res[log.name] = filename
        return res

    def _get_script_fname(self, extension='.sh'):
        return os.path.join(self.workdir, "exec{}".format(extension))

    def _prepare_parameters(self, pythonize=False):
        res = {}
        for parameter in self.node.parameters:
            value = None
            if parameter.parameter_type == ParameterTypes.ENUM:
                index = max(0, min(len(parameter.value.values) - 1, parameter.value.index))
                value = parameter.value.values[index]
            elif parameter.parameter_type in [ParameterTypes.LIST_STR, ParameterTypes.LIST_INT]:
                if pythonize:
                    value = parameter.value
                else:
                    value = ' '.join(map(str, parameter.value))  # !!!!!!!!!
            elif parameter.parameter_type == ParameterTypes.CODE:
                value = parameter.value.value
            else:
                value = parameter.value
            res[parameter.name] = value
        return res

    def _postprocess_outputs(self, outputs):
        for key, filename in outputs.items():
            if os.path.exists(filename):
                matching_outputs = filter(lambda o: o.name == key, self.node.outputs)
                assert len(matching_outputs) == 1, "Found more that 1 output with the same name `{}`".format(key)
                if matching_outputs[0].file_type == FileTypes.DIRECTORY:
                    zip_filename = '{}.zip'.format(filename)
                    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zf:
                        zipdir(filename, zf)
                    filename = zip_filename
                with open(filename, 'rb') as f:
                    self.node.get_output_by_name(key).resource_id = upload_file_stream(f)

    def _postprocess_logs(self, logs):
        for key, filename in logs.items():
            if os.path.exists(filename) and os.stat(filename).st_size != 0:
                with open(filename, 'rb') as f:
                    self.node.get_log_by_name(key).resource_id = upload_file_stream(f)
