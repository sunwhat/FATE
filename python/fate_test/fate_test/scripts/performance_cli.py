#
#  Copyright 2019 The FATE Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import os
import time
import uuid
from datetime import timedelta
import click
from fate_test import _config
from fate_test._client import Clients
from fate_test._config import Config
from fate_test._flow_client import JobProgress, SubmitJobResponse, QueryJobResponse
from fate_test._io import LOGGER, echo
from fate_test._parser import JSON_STRING, Testsuite
from fate_test.scripts._options import SharedOptions
from fate_test.scripts._utils import _load_testsuites, _upload_data, _delete_data, _load_module_from_script, \
    _add_replace_hook, _big_data_task


@click.command("performance")
@click.option('-t', '--task', required=True, type=click.Choice(['upload', 'intersect', 'intersect_multi', 'hetero_lr',
                                                                'hetero_sbt']),
              help="Select the task type, you can also set through include")
@click.option('-i', '--include', type=click.Path(exists=True), multiple=True, metavar="<include>",
              help="include *testsuite.json under these paths")
@click.option('-et', '--encryption_type', type=click.Choice(['sha256', 'md5']),
              help="Encryption types include sha256 and md5")
@click.option('-mr', '--match_rate', default=1.0, type=float,
              help="Intersection rate relative to guest, The value is between (0-1)")
@click.option('-s', '--sparsity', default=0.2, type=float,
              help="The sparsity of tag data, The value is between (0-1)")
@click.option('-r', '--replace', default="{}", type=JSON_STRING,
              help="a json string represents mapping for replacing fields in data/conf/dsl")
@click.option('-time', '--timeout', type=int, default=3600,
              help="Task timeout duration")
@click.option('-iter', '--max_iter', type=int, default=100,
              help="When the algorithm model is LR, the number of iterations is set")
@click.option('-depth', '--max_depth', type=int, default=4,
              help="When the algorithm model is SecureBoost, set the number of model layers")
@click.option('-trees', '--num_trees', type=int, default=100,
              help="When the algorithm model is SecureBoost, set the number of trees")
@click.option('-node', '--processors_per_node', type=int, default=4,
              help="processors per node")
@click.option('-jp', '--update_job_parameters', default="{}", type=JSON_STRING,
              help="a json string represents mapping for replacing fields in conf.job_parameters")
@click.option('-cp', '--update_component_parameters', default="{}", type=JSON_STRING,
              help="a json string represents mapping for replacing fields in conf.component_parameters")
@click.option('-ng', '--guest_data_size', required=True, type=int,
              help="Set guest data set size")
@click.option('-nh', '--host_data_size', type=int, default=0,
              help="Set host data set size, The default is equal to the number of guests")
@click.option('-fg', '--guest_feature_num', type=int, default=20,
              help="Set guest feature dimensions")
@click.option('-fh', '--host_feature_num', type=int, default=200,
              help="Set host feature dimensions, The default is equal to the number of guests")
@click.option('-u', '--use_local_data', type=int, default=1,
              help="When guest, host and flow are deployed on the same machine, the parameter 0 is more appropriate")
@click.option("--disable-clean-data", "clean_data", flag_value=False, default=None)
@click.option("--enable-clean-data", "clean_data", flag_value=True, default=None)
@SharedOptions.get_shared_options(hidden=True)
@click.pass_context
def run_task(ctx, task, include, encryption_type, match_rate, sparsity, guest_data_size, host_data_size,
             guest_feature_num, host_feature_num, replace, timeout, update_job_parameters, use_local_data,
             update_component_parameters, max_iter, max_depth, num_trees, processors_per_node, clean_data, **kwargs):
    """
    Test the performance of big data tasks
    """
    ctx.obj.update(**kwargs)
    ctx.obj.post_process()
    config_inst = ctx.obj["config"]
    if clean_data is None:
        clean_data = config_inst.clean_data
    namespace = ctx.obj["namespace"]
    yes = ctx.obj["yes"]
    data_namespace_mangling = ctx.obj["namespace_mangling"]
    if use_local_data not in [0, 1]:
        raise Exception("'use_local_data 'can only be 0 or 1")
    _config.use_local_data = use_local_data

    def get_cache_directory(conf: Config):
        return conf.cache_directory

    if not include:
        if task == 'intersect':
            include = os.path.join(str(get_cache_directory(config_inst)), _config.intersect_dir)
        if task == 'intersect_multi':
            include = os.path.join(str(get_cache_directory(config_inst)), _config.intersect_multi_dir)
        elif task == 'hetero_lr':
            include = os.path.join(str(get_cache_directory(config_inst)), _config.hetero_lr_dir)
        elif task == 'hetero_sbt':
            include = os.path.join(str(get_cache_directory(config_inst)), _config.hetero_sbt_dir)
        elif task == 'upload':
            include = os.path.join(str(get_cache_directory(config_inst)), _config.upload_dir)
        include = (include,)
    if not host_data_size:
        host_data_size = guest_data_size
    if guest_data_size < 0 or host_data_size < 0:
        raise Exception('The number of samples in the dataset must not be less than 0')
    else:
        _config.data_switch = guest_data_size != 0 and host_data_size != 0
    # prepare output dir and json hooks
    _add_replace_hook(replace)

    echo.welcome()
    echo.echo(f"testsuite namespace: {namespace}", fg='red')
    echo.echo("loading testsuites:")
    suites = _load_testsuites(includes=include, excludes=tuple(), glob=None)
    for suite in suites:
        echo.echo(f"\tdataget({len(suite.dataset)}) dataset({len(suite.dataset)}) dsl jobs({len(suite.jobs)}) "
                  f"pipeline jobs ({len(suite.pipeline_jobs)}) {suite.path}")
    if not yes and not click.confirm("running?"):
        return

    echo.stdout_newline()
    if _config.data_switch:
        _big_data_task(task, guest_data_size, host_data_size, guest_feature_num, host_feature_num, include, config_inst,
                       encryption_type, match_rate, sparsity)
    with Clients(config_inst) as client:

        for i, suite in enumerate(suites):
            # noinspection PyBroadException
            try:
                start = time.time()
                echo.echo(f"[{i + 1}/{len(suites)}]start at {time.strftime('%Y-%m-%d %X')} {suite.path}", fg='red')

                try:
                    _upload_data(client, suite, config_inst)
                except Exception as e:
                    raise RuntimeError(f"exception occur while uploading data for {suite.path}") from e

                echo.stdout_newline()
                try:
                    _submit_job(client, suite, namespace, config_inst, timeout, update_job_parameters,
                                update_component_parameters, task, max_iter, max_depth, num_trees, processors_per_node)
                except Exception as e:
                    raise RuntimeError(f"exception occur while submit job for {suite.path}") from e

                try:
                    _run_pipeline_jobs(config_inst, suite, namespace, data_namespace_mangling)
                except Exception as e:
                    raise RuntimeError(f"exception occur while running pipeline jobs for {suite.path}") from e
                if task != 'upload' and clean_data:
                    _delete_data(client, suite)
                echo.echo(f"[{i + 1}/{len(suites)}]elapse {timedelta(seconds=int(time.time() - start))}", fg='red')

                echo.echo(suite.pretty_final_summary(), fg='red')

            except Exception:
                exception_id = uuid.uuid1()
                echo.echo(f"exception in {suite.path}, exception_id={exception_id}")
                LOGGER.exception(f"exception id: {exception_id}")
            finally:
                echo.stdout_newline()

    echo.farewell()
    echo.echo(f"testsuite namespace: {namespace}", fg='red')


def _submit_job(clients: Clients, suite: Testsuite, namespace: str, config: Config, timeout, update_job_parameters,
                update_component_parameters, task, max_iter, max_depth, num_trees, processors_per_node):
    # submit jobs
    with click.progressbar(length=len(suite.jobs),
                           label="jobs",
                           show_eta=False,
                           show_pos=True,
                           width=24) as bar:
        for job in suite.jobs_iter():
            job_progress = JobProgress(job.job_name)

            def _raise():
                exception_id = str(uuid.uuid1())
                job_progress.exception(exception_id)
                suite.update_status(job_name=job.job_name, exception_id=exception_id)
                echo.file(f"exception({exception_id})")
                LOGGER.exception(f"exception id: {exception_id}")

            # noinspection PyBroadException
            try:
                if task == "hetero_lr":
                    job.job_conf.update_component_parameters('max_iter', max_iter)
                elif task == "hetero_sbt":
                    job.job_conf.update_component_parameters('max_depth', max_depth)
                    job.job_conf.update_component_parameters('num_trees', num_trees)
                job.job_conf.update_job_common_parameters(
                    eggroll_run={"eggroll.session.processors.per.node": processors_per_node})
                job.job_conf.update(config.parties, config.work_mode, config.backend, timeout, update_job_parameters,
                                    update_component_parameters)
            except Exception:
                _raise()
                continue

            def update_bar(n_step):
                bar.item_show_func = lambda x: job_progress.show()
                time.sleep(0.1)
                bar.update(n_step)

            update_bar(1)

            def _call_back(resp: SubmitJobResponse):
                if isinstance(resp, SubmitJobResponse):
                    job_progress.submitted(resp.job_id)
                    echo.file(f"[jobs] {resp.job_id} ", nl=False)
                    suite.update_status(job_name=job.job_name, job_id=resp.job_id)

                if isinstance(resp, QueryJobResponse):
                    job_progress.running(resp.status, resp.progress)

                update_bar(0)

            # noinspection PyBroadException
            try:
                response = clients["guest_0"].submit_job(job=job, callback=_call_back)

                # noinspection PyBroadException
                try:
                    # add notes
                    notes = f"{job.job_name}@{suite.path}@{namespace}"
                    for role, party_id_list in job.job_conf.role.items():
                        for i, party_id in enumerate(party_id_list):
                            clients[f"{role}_{i}"].add_notes(job_id=response.job_id, role=role, party_id=party_id,
                                                             notes=notes)
                except Exception:
                    pass
            except Exception:
                _raise()
            else:
                job_progress.final(response.status)
                suite.update_status(job_name=job.job_name, status=response.status.status)
                if response.status.is_success():
                    if suite.model_in_dep(job.job_name):
                        dependent_jobs = suite.get_dependent_jobs(job.job_name)
                        for predict_job in dependent_jobs:
                            if predict_job.job_conf.dsl_version == 2:
                                # noinspection PyBroadException
                                try:
                                    model_info = clients["guest_0"].deploy_model(
                                        model_id=response.model_info["model_id"],
                                        model_version=response.model_info["model_version"],
                                        dsl=predict_job.job_dsl.as_dict())
                                except Exception:
                                    _raise()
                            else:
                                model_info = response.model_info
                            suite.feed_dep_model_info(predict_job, job.job_name, model_info)
                        suite.remove_dependency(job.job_name)
            update_bar(0)
            echo.stdout_newline()

def _run_pipeline_jobs(config: Config, suite: Testsuite, namespace: str, data_namespace_mangling: bool):
    # pipeline demo goes here
    job_n = len(suite.pipeline_jobs)
    for i, pipeline_job in enumerate(suite.pipeline_jobs):
        echo.echo(f"Running [{i + 1}/{job_n}] job: {pipeline_job.job_name}")

        def _raise(err_msg, status="failed"):
            exception_id = str(uuid.uuid1())
            suite.update_status(job_name=job_name, exception_id=exception_id, status=status)
            echo.file(f"exception({exception_id}), error message:\n{err_msg}")
            # LOGGER.exception(f"exception id: {exception_id}")

        job_name, script_path = pipeline_job.job_name, pipeline_job.script_path
        mod = _load_module_from_script(script_path)
        try:
            if data_namespace_mangling:
                try:
                    mod.main(config=config, namespace=f"_{namespace}")
                    suite.update_status(job_name=job_name, status="success")
                except Exception as e:
                    _raise(e)
                    continue
            else:
                try:
                    mod.main(config=config)
                    suite.update_status(job_name=job_name, status="success")
                except Exception as e:
                    _raise(e)
                    continue
        except Exception as e:
            _raise(e, status="not submitted")
            continue