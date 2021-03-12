
import logging
import os
import time
import sys

from http import HTTPStatus
from modules.streams_service_api_helper import DSPApi
from modules.utils import manipulate_spl, read_spl, read_data


# Logger
logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
LOGGER = logging.getLogger(__name__)

# MACROS
SLEEP_TIME_CREATE_INDEX = 10
SLEEP_TIME_ACTIVATE_PIPELINE = 10
SLEEP_TIME_SEND_DATA = 30
WAIT_CYCLE = 20
MAX_EXECUTION_TIME_LIMIT = 1200  # per detection test

TEST_DATASET = 'windows-security_small.txt'

class SSADetectionTesting:

    def __init__(self, env, tenant, header_token):
        self.execution_passed = True
        self.max_execution_time = MAX_EXECUTION_TIME_LIMIT
        self.env = env
        self.tenant = tenant
        self.header_token = f"Bearer {header_token}"
        self.api = DSPApi(env, tenant, self.header_token)
        self.test_results = {}

    def test_dsp_pipeline(self):

        file_path_data = os.path.join(os.path.dirname(__file__), 'data', TEST_DATASET)
        file_path_spl = os.path.join(os.path.dirname(__file__), 'spl')

        test_spls = ['troubleshoot.spl', 'detection.spl']
        test_names = [
            "SSA Index Test Minimal",
            "SSA Index Detection Testing Example"
        ]

        test_results = []
        for i in range(0,len(test_spls)):
            self.max_execution_time = MAX_EXECUTION_TIME_LIMIT
            test_result = self.ssa_detection_test(read_spl(file_path_spl, test_spls[i]), file_path_data, test_names[i])
            test_results.append(test_result.copy())

        passed = True

        LOGGER.info('-----------------------------------')
        LOGGER.info('-------- test DSP Pipeline --------')
        LOGGER.info('-----------------------------------')
        for test_result in test_results:
            LOGGER.info(test_result['msg'])
            passed = passed and test_result['result']
        LOGGER.info('-----------------------------------')

        return passed


    def test_ssa_detections(self, test_obj):

        self.max_execution_time = MAX_EXECUTION_TIME_LIMIT
        file_path_attack_data = os.path.join(os.path.dirname(__file__), "../", test_obj["attack_data_file_path"])

        test_results = self.ssa_detection_test(test_obj["detection_obj"]["search"], file_path_attack_data, "SSA Smoke Test " + test_obj["test_obj"]["name"])

        return test_results


    ## Helper Functions ##

    def update_execution_time(self, time_frame):
        self.max_execution_time = self.max_execution_time - WAIT_CYCLE
        if self.max_execution_time < 0:
            return True
        else:
            return False

    def wait_time(self, time_in_s):
        time.sleep(time_in_s)
        return self.update_execution_time(time_in_s)

    def check_result(self, condition, error_message):
        try:
            assert condition
        except:
            self.execution_passed = False
            LOGGER.error(error_message)

    def write_test_results(self, test_name):
        if not self.execution_passed:
            msg = f"Detection test failed for {test_name}"
            LOGGER.error(msg)
            self.test_results["msg"] = msg
            self.test_results["result"] = False
        else:
            msg = f"Detection test successful for {test_name}"
            LOGGER.info(msg)
            self.test_results["msg"] = msg


    def ssa_detection_test_init(self):
        self.test_results["result"] = True
        self.test_results["msg"] = ""
        self.results_index = self.api.create_temp_index("mc")


    def ssa_detection_test_main(self, spl, source, test_name):
        self.execution_passed = True

        self.wait_time(SLEEP_TIME_CREATE_INDEX)

        spl = manipulate_spl(self.api.env, spl, self.results_index)
        self.check_result(spl is not None, "fail to manipulate spl file")

        pipeline_id = self.api.create_pipeline_from_spl(spl)
        self.check_result(pipeline_id is not None, "failed to create a pipeline")

        _pipeline_status = self.api.pipeline_status(pipeline_id)
        self.check_result(_pipeline_status=="CREATED", f"Current status of pipeline {pipeline_id} should be CREATED")

        response_body = self.api.activate_pipeline(pipeline_id)
        self.check_result(response_body.get("activated")==pipeline_id, f"pipeline {pipeline_id} should be successfully activate.")

        self.wait_time(SLEEP_TIME_ACTIVATE_PIPELINE)

        data = read_data(source)
        response_body = self.api.ingest_data(data)

        self.wait_time(SLEEP_TIME_SEND_DATA)

        search_results = False
        max_execution_time_reached = False

        while not (search_results or max_execution_time_reached):
            max_execution_time_reached = self.wait_time(WAIT_CYCLE)
            query = f"from index:{self.results_index['name']} | search source!=\"Search Catalog\" "
            sid = self.api.submit_search_job(self.results_index['module'], query)
            self.check_result(sid is not None, f"Failed to create a Search Job")
            
            job_finished = False
            while not job_finished:
                self.wait_time(WAIT_CYCLE)
                result = self.api.check_search_job_finished(sid)
                job_finished = result

            results = self.api.get_search_job_results(sid)
            search_results = (len(results) > 0)
            if not search_results:
                LOGGER.info(f"Search didn't return any results. Retrying in {WAIT_CYCLE}s, max execution time left {self.max_execution_time}s")
        
        self.check_result(len(results) > 0, "Search job didn't return any results")

        response, response_body = self.api.deactivate_pipeline(pipeline_id)
        self.check_result(response.status_code == HTTPStatus.OK, f"The pipeline {pipeline_id} fails to deactivated.")

        response = self.api.delete_pipeline(pipeline_id)
        self.check_result(response.status_code == HTTPStatus.NO_CONTENT, f"Fail to delete pipeline {pipeline_id}.")

        self.write_test_results(test_name)

        return self.test_results


    def ssa_detection_test_teardown(self):
        pass
        #self.api.delete_temp_index(self.results_index["id"])


    def ssa_detection_test(self, spl, source, test_name):
        self.ssa_detection_test_init()
        test_result = self.ssa_detection_test_main(spl, source, test_name)
        self.ssa_detection_test_teardown()
        return test_result



    # only for troubleshooting
    # def ssa_detection_in_dsp_with_preview_session(self, spl, source, test_name):

    #     self.execution_passed = True

    #     spl = manipulate_spl(self.api.env, spl)
    #     self.check_result(spl is not None, "fail to read dummy spl file")

    #     preview_id = self.api.get_preview_id_from_spl(spl)
    #     self.check_result(preview_id is not None, "failed to create a preview session %s" % spl)

    #     time.sleep(SLEEP_TIME_SHORT)

    #     data = read_data(source)
    #     response_body = self.api.ingest_data(data)

    #     time.sleep(SLEEP_TIME_LONG)

    #     response, response_body = self.api.get_preview_data(preview_id)
    #     self.check_result(response_body.get("currentNumberOfRecords") > 0, "Missing records in preview session.")

    #     response = self.api.stop_preview_session(preview_id)

    #     self.write_test_results(test_name)