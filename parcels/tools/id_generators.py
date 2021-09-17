import random   # could be python's random if parcels not active; can be parcel's random; can be numpy's random
from abc import ABC, abstractmethod
# from numpy import random as nprandom
# from multiprocessing import Process
from threading import Thread
from .message_service import mpi_execute_requested_messages as executor
# from os import getpid
import numpy as np

try:
    from mpi4py import MPI
except:
    MPI = None


class BaseIdGenerator(ABC):
    _total_ids = 0
    _used_ids = 0
    _recover_ids = False
    _map_id_totalindex = dict()
    _track_id_index = True

    def __init__(self):
        self._total_ids = 0
        self._used_ids = 0
        self._track_id_index = True

    def setTimeLine(self, min_time, max_time):
        pass

    def setDepthLimits(self, min_depth, max_depth):
        pass

    def preGenerateIDs(self, high_value):
        pass

    def permuteIDs(self):
        pass

    def close(self):
        pass

    @abstractmethod
    def __len__(self):
        pass

    @property
    def total_length(self):
        return self._total_ids

    @property
    def usable_length(self):
        return self._used_ids

    @property
    def recover_ids(self):
        return self._recover_ids

    @recover_ids.setter
    def recover_ids(self, bool_param):
        self._recover_ids = bool_param

    def enable_ID_recovery(self):
        self._recover_ids = True

    def disable_ID_recovery(self):
        self._recover_ids = False

    @abstractmethod
    def getID(self, lon, lat, depth, time):
        pass

    def nextID(self, lon, lat, depth, time):
        return self.getID(lon, lat, depth, time)

    @abstractmethod
    def releaseID(self, id):
        pass

    @abstractmethod
    def get_length(self):
        return self.__len__()

    def get_total_length(self):
        return self._total_ids

    def get_usable_length(self):
        return self._used_ids

    def enable_id_index_tracking(self):
        self._track_id_index = True

    def disable_id_index_tracking(self):
        self._track_id_index = False

    def map_id_to_index(self, input_id):
        if self._track_id_index:
            return self._map_id_totalindex[input_id]
        return None

    def is_tracking_id_index(self):
        return self._track_id_index


class SequentialIdGenerator(BaseIdGenerator):
    released_ids = []
    next_id = 0

    def __init__(self):
        super(SequentialIdGenerator, self).__init__()
        self.released_ids = []
        self.next_id = np.uint64(0)
        self._recover_ids = False

    def __del__(self):
        if len(self.released_ids) > 0:
            del self.released_ids

    def getID(self, lon, lat, depth, time):
        n = len(self.released_ids)
        if n == 0:
            result = self.next_id
            self.next_id += 1
            if self._track_id_index:
                self._map_id_totalindex[result] = self._total_ids
            self._total_ids += 1
            self._used_ids += 1
            return np.uint64(result)
        else:
            result = self.released_ids.pop(n-1)
            if self._track_id_index:
                self._map_id_totalindex[result] = self._total_ids
            self._used_ids += 1
            self._total_ids += 1
            return np.uint64(result)

    def releaseID(self, id):
        if not self._recover_ids:
            return
        self.released_ids.append(id)
        self._used_ids -= 1

    def preGenerateIDs(self, high_value):
        if len(self.released_ids) > 0:
            self.released_ids.clear()
        self.released_ids = [i for i in range(0, high_value)]
        self.next_id = high_value

    def permuteIDs(self):
        n = len(self.released_ids)
        indices = random.randint(0, n, 2*n)
        for index in indices:
            id = self.released_ids.pop(index)
            self.released_ids.append(id)

    def __len__(self):
        return self.next_id

    def get_length(self):
        return len(self)


class SpatialIdGenerator(BaseIdGenerator):
    """Generates 64-bit IDs"""
    _lon_bins = 360
    _lat_bins = 180
    _depth_bins = 32768
    _lonbounds = np.zeros(2, dtype=np.float32)
    _latbounds = np.zeros(2, dtype=np.float32)
    _depthbounds = np.zeros(2, dtype=np.float32)
    local_ids = None
    released_ids = {}

    def __init__(self, lon_bins=360, lat_bins=180, depth_bins=32768):
        """
        ID generator that manages IDs in a spatial mapping scheme, so that IDs
        that are spatially close are also numerically close.

        Attention: the bins are used in a bit allocation scheme so that
        (log2(arg:lon_bins) * long2(arg::lat_bins) * log2(arg:depth_bins)) <= 32
        """
        super(SpatialIdGenerator, self).__init__()
        self._lonbounds = np.array([-180.0, 180.0], dtype=np.float32)
        self._latbounds = np.array([-90.0, 90.0], dtype=np.float32)
        self._depthbounds = np.array([0.0, 1.0], dtype=np.float32)
        self._lon_bins = lon_bins
        self._lat_bins = lat_bins
        self._depth_bins = depth_bins
        self.local_ids = np.zeros((self._lon_bins, self._lat_bins, self._depth_bins), dtype=np.uint32)
        self.released_ids = {}  # 32-bit spatio-temporal index => []
        self._recover_ids = False

    def __del__(self):
        if self.local_ids is not None:
            del self.local_ids
        if len(self.released_ids) > 0:
            del self.released_ids

    def setLonLimits(self, min_lon=-180.0, max_lon=180.0):
        self._lonbounds = np.array([min_lon, max_lon], dtype=np.float32)

    def setLatLimits(self, min_lat=-90.0, max_lat=90.0):
        self._latbounds = np.array([min_lat, max_lat], dtype=np.float32)

    def setDepthLimits(self, min_depth=0.0, max_depth=1.0):
        self._depthbounds = np.array([min_depth, max_depth], dtype=np.float32)

    def getID(self, lon, lat, depth, time=None):
        idlon = lon  # avoid original 'lon' changes from change-by-ref artefacts
        idlat = lat  # avoid original 'lat' changes from change-by-ref artefacts
        iddepth = depth  # avoid original 'depth' changes from change-by-ref artefacts
        if idlon < self._lonbounds[0]:
            vsgn = np.sign(idlon)
            idlon = np.fmod(np.fabs(idlon), np.fabs(self._lonbounds[0])) * vsgn
        if idlon > self._lonbounds[1]:
            vsgn = np.sign(idlon)
            idlon = np.fmod(np.fabs(idlon), np.fabs(self._lonbounds[1])) * vsgn
        if idlat < self._latbounds[0]:
            vsgn = np.sign(idlat)
            idlat = np.fmod(np.fabs(idlat), np.fabs(self._latbounds[0])) * vsgn
        if idlat > self._latbounds[1]:
            vsgn = np.sign(idlat)
            idlat = np.fmod(np.fabs(idlat), np.fabs(self._latbounds[1])) * vsgn
        if iddepth is None:
            iddepth = self._depthbounds[0]
        if iddepth < self._depthbounds[0] or depth > self._depthbounds[1]:
            vsgn = np.sign(depth)
            iddepth = np.fmod(np.fabs(iddepth), np.fabs(max(self._depthbounds))) * vsgn if min(self._depthbounds) > 0 else max(self._depthbounds) - (np.fmod(np.fabs(iddepth), max(np.fabs(self._depthbounds))) * vsgn)
        lon_discrete = (idlon - self._lonbounds[0]) / (self._lonbounds[1] - self._lonbounds[0])
        lon_discrete = np.int32((self._lon_bins-1) * lon_discrete)
        lat_discrete = (idlat - self._latbounds[0]) / (self._latbounds[1] - self._latbounds[0])
        lat_discrete = np.int32((self._lat_bins-1) * lat_discrete)
        depth_discrete = (iddepth - self._depthbounds[0])/(self._depthbounds[1]-self._depthbounds[0])
        depth_discrete = np.int32((self._depth_bins-1) * depth_discrete)
        lon_index = np.uint32(np.int32(lon_discrete))
        lat_index = np.uint32(np.int32(lat_discrete))
        depth_index = np.uint32(np.int32(depth_discrete))
        id = self._get_next_id(lon_index, lat_index, depth_index, None)
        return id

    def nextID(self, lon, lat, depth, time):
        return self.getID(lon, lat, depth, time)

    def releaseID(self, id):
        full_bits = np.uint32(4294967295)
        nil_bits = np.int32(0)
        spatiotemporal_id = np.bitwise_and(np.bitwise_or(np.left_shift(np.int64(full_bits), 32), np.int64(nil_bits)), np.int64(id))
        spatiotemporal_id = np.uint32(np.right_shift(spatiotemporal_id, 32))
        local_id = np.bitwise_and(np.bitwise_or(np.left_shift(np.int64(nil_bits), 32), np.int64(full_bits)), np.int64(id))
        local_id = np.uint32(local_id)
        self._release_id(spatiotemporal_id, local_id)

    def __len__(self):
        return np.sum(self.local_ids) + sum([len(entity) for entity in self.released_ids])

    def get_length(self):
        return self.__len__()

    def _get_next_id(self, lon_index, lat_index, depth_index, time_index=None):
        local_index = -1
        lon_shift = 32-int(np.ceil(np.log2(self._lon_bins)))
        lat_shift = lon_shift-int(np.ceil(np.log2(self._lat_bins)))
        id = np.left_shift(lon_index, lon_shift) + np.left_shift(lat_index, lat_shift) + depth_index
        if len(self.released_ids) > 0 and (id in self.released_ids.keys()) and len(self.released_ids[id]) > 0:
            local_index = np.uint32(self.released_ids[id].pop())
            if len(self.released_ids[id]) <= 0:
                del self.released_ids[id]
        else:
            local_index = self.local_ids[lon_index, lat_index, depth_index]
            self.local_ids[lon_index, lat_index, depth_index] += 1
        id = np.int64(id)
        id = np.bitwise_or(np.left_shift(id, 32), np.int64(local_index))
        id = np.uint64(id)
        if self._track_id_index:
            self._map_id_totalindex[id] = self._total_ids
        self._total_ids += 1
        self._used_ids += 1
        return id

    def _release_id(self, spatiotemporal_id, local_id):
        if not self._recover_ids:
            return
        if spatiotemporal_id not in self.released_ids.keys():
            self.released_ids[spatiotemporal_id] = []
        self.released_ids[spatiotemporal_id].append(local_id)
        self._used_ids -= 1


class SpatioTemporalIdGenerator(BaseIdGenerator):
    """Generates 64-bit IDs"""
    timebounds = np.zeros(2, dtype=np.float64)
    depthbounds = np.zeros(2, dtype=np.float32)
    local_ids = None
    released_ids = {}

    def __init__(self):
        super(SpatioTemporalIdGenerator, self).__init__()
        self._timebounds = np.array([0, 1.0], dtype=np.float64)
        self._depthbounds = np.array([0, 1.0], dtype=np.float32)
        self.local_ids = np.zeros((360, 180, 128, 256), dtype=np.uint32)
        self.released_ids = {}  # 32-bit spatio-temporal index => []
        self._recover_ids = False

    def __del__(self):
        if self.local_ids is not None:
            del self.local_ids
        if len(self.released_ids) > 0:
            del self.released_ids

    def setTimeLine(self, min_time=0.0, max_time=1.0):
        self._timebounds = np.array([min_time, max_time], dtype=np.float64)

    def setDepthLimits(self, min_depth=0.0, max_depth=1.0):
        self._depthbounds = np.array([min_depth, max_depth], dtype=np.float32)

    def getID(self, lon, lat, depth, time):
        idlon = lon  # avoid original 'lon' changes from change-by-ref artefacts
        idlat = lat  # avoid original 'lat' changes from change-by-ref artefacts
        iddepth = depth  # avoid original 'depth' changes from change-by-ref artefacts
        idtime = time  # avoid original 'time' changes from change-by-ref artefacts
        if idlon < -180.0 or idlon > 180.0:
            vsgn = np.sign(idlon)
            idlon = np.fmod(np.fabs(idlon), 180.0) * vsgn
        if idlat < -90.0 or idlat > 90.0:
            vsgn = np.sign(idlat)
            idlat = np.fmod(np.fabs(idlat), 90.0) * vsgn
        if iddepth is None:
            iddepth = self._depthbounds[0]
        if iddepth < self._depthbounds[0] or iddepth > self._depthbounds[1]:
            vsgn = np.sign(iddepth)
            iddepth = np.fmod(np.fabs(iddepth), np.fabs(max(self._depthbounds))) * vsgn if min(self._depthbounds) > 0 else max(self._depthbounds) - (np.fmod(np.fabs(iddepth), max(np.fabs(self._depthbounds))) * vsgn)
        if idtime is None:
            idtime = self._timebounds[0]
        if idtime < self._timebounds[0] or idtime > self._timebounds[1]:
            vsgn = np.sign(idtime)
            idtime = np.fmod(np.fabs(idtime), np.fabs(max(self._timebounds))) * vsgn if min(self._timebounds) > 0 else max(self._timebounds) - (np.fmod(np.fabs(idtime), max(np.fabs(self._timebounds))) * vsgn)
        lon_discrete = np.int32(min(max(idlon, -179.9), 179.9))
        lat_discrete = np.int32(min(max(idlat, -179.9), 179.9))
        depth_discrete = (iddepth-self._depthbounds[0])/(self._depthbounds[1]-self._depthbounds[0])
        depth_discrete = np.int32(127.0 * depth_discrete)
        time_discrete = (idtime-self._timebounds[0])/(self._timebounds[1]-self._timebounds[0])
        time_discrete = np.int32(255.0 * time_discrete)
        lon_index = np.uint32(np.int32(lon_discrete)+180)
        lat_index = np.uint32(np.int32(lat_discrete)+90)
        depth_index = np.uint32(np.int32(depth_discrete))
        time_index = np.uint32(np.int32(time_discrete))
        id = self._get_next_id(lon_index, lat_index, depth_index, time_index)
        return id

    def nextID(self, lon, lat, depth, time):
        return self.getID(lon, lat, depth, time)

    def releaseID(self, id):
        full_bits = np.uint32(4294967295)
        nil_bits = np.int32(0)
        spatiotemporal_id = np.bitwise_and(np.bitwise_or(np.left_shift(np.int64(full_bits), 32), np.int64(nil_bits)), np.int64(id))
        spatiotemporal_id = np.uint32(np.right_shift(spatiotemporal_id, 32))
        local_id = np.bitwise_and(np.bitwise_or(np.left_shift(np.int64(nil_bits), 32), np.int64(full_bits)), np.int64(id))
        local_id = np.uint32(local_id)
        self._release_id(spatiotemporal_id, local_id)

    def __len__(self):
        return np.sum(self.local_ids) + sum([len(entity) for entity in self.released_ids])

    def get_length(self):
        return self.__len__()

    def _get_next_id(self, lon_index, lat_index, depth_index, time_index):
        local_index = -1
        id = np.left_shift(lon_index, 23) + np.left_shift(lat_index, 15) + np.left_shift(depth_index, 8) + time_index
        if len(self.released_ids) > 0 and (id in self.released_ids.keys()) and len(self.released_ids[id]) > 0:
            local_index = np.uint32(self.released_ids[id].pop())
            if len(self.released_ids[id]) <= 0:
                del self.released_ids[id]
        else:
            local_index = self.local_ids[lon_index, lat_index, depth_index, time_index]
            self.local_ids[lon_index, lat_index, depth_index, time_index] += 1
        id = np.int64(id)
        id = np.bitwise_or(np.left_shift(id, 32), np.int64(local_index))
        id = np.uint64(id)
        if self._track_id_index:
            self._map_id_totalindex[id] = self._total_ids
        self._total_ids += 1
        self._used_ids += 1
        return id

    def _release_id(self, spatiotemporal_id, local_id):
        if not self._recover_ids:
            return
        if spatiotemporal_id not in self.released_ids.keys():
            self.released_ids[spatiotemporal_id] = []
        self.released_ids[spatiotemporal_id].append(local_id)
        self._used_ids -= 1


class GenerateID_Service(BaseIdGenerator):
    _request_tag = 5
    _response_tag = 6

    def __init__(self, base_generator_obj):
        super(GenerateID_Service, self).__init__()
        self._service_process = None
        self._serverrank = 0
        self._request_tag = 5
        self._response_tag = 6
        self._recover_ids = False
        self._use_subprocess = True

        if MPI:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()
            mpi_size = mpi_comm.Get_size()
            if mpi_size <= 1:
                self._use_subprocess = False
            else:
                self._serverrank = mpi_size-1
                if mpi_rank == self._serverrank:
                    # self._service_process = Process(target=executor, name="IdService", args=(service_bundle, base_generator_obj), daemon=True)
                    # self._service_process.start()
                    # print("Starting ID service process")
                    # logger.info("Starting ID service process")
                    self._service_process = Thread(target=executor, name="IdService", args=(base_generator_obj, self._request_tag, self._response_tag), daemon=True)
                    # self._service_process.daemon = True
                    self._service_process.start()
                    # executor(base_generator_obj, self._request_tag, self._response_tag)
                # mpi_comm.Barrier()
                # logger.info("worker - MPI rank: {} pid: {}".format(mpi_rank, getpid()))
                self._subscribe_()
        else:
            self._use_subprocess = False

        if not self._use_subprocess:
            self._service_process = base_generator_obj()

    def __del__(self):
        self._abort_()

    def _subscribe_(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()
            data_package = {}
            data_package["func_name"] = "thread_subscribe"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)

    def _abort_(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()
            data_package = {}
            data_package["func_name"] = "thread_abort"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)

    def close(self):
        self._abort_()

    def enable_ID_recovery(self):
        self._recover_ids = True
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "enable_ID_recovery"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.enable_ID_recovery()

    def disable_ID_recovery(self):
        self._recover_ids = False
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "disable_ID_recovery"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.disable_ID_recovery()

    def setTimeLine(self, min_time=0.0, max_time=1.0):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()
            if mpi_rank == 0:
                data_package = {}
                data_package["func_name"] = "setTimeLine"
                data_package["args"] = 2
                data_package["argv"] = [min_time, max_time]
                data_package["src_rank"] = mpi_rank
                mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.setTimeLine(min_time, max_time)

    def setDepthLimits(self, min_depth=0.0, max_depth=1.0):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()
            if mpi_rank == 0:
                data_package = {}
                data_package["func_name"] = "setDepthLimits"
                data_package["args"] = 2
                data_package["argv"] = [min_depth, max_depth]
                data_package["src_rank"] = mpi_rank
                mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.setDepthLimits(min_depth, max_depth)

    def getID(self, lon, lat, depth, time):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "getID"
            data_package["args"] = 4
            data_package["argv"] = [lon, lat, depth, time]
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
            data = mpi_comm.recv(source=self._serverrank, tag=self._response_tag)
            return int(data["result"])
        else:
            return self._service_process.getID(lon, lat, depth, time)

    def nextID(self, lon, lat, depth, time):
        return self.getID(lon, lat, depth, time)

    def releaseID(self, id):
        if not self._recover_ids:
            return
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "releaseID"
            data_package["args"] = 1
            data_package["argv"] = [id, ]
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.releaseID(id)

    def get_length(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "get_length"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
            data = mpi_comm.recv(source=self._serverrank, tag=self._response_tag)

            return int(data["result"])
        else:
            return self._service_process.__len__()

    def get_total_length(self):
        # raise NotImplementedError()
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "get_total_length"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
            data = mpi_comm.recv(source=self._serverrank, tag=self._response_tag)

            return int(data["result"])
        else:
            return self._service_process.get_total_length()

    def get_usable_length(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "get_usable_length"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
            data = mpi_comm.recv(source=self._serverrank, tag=self._response_tag)

            return int(data["result"])
        else:
            return self._service_process.get_usable_length()

    def __len__(self):
        return self.get_length()

    @property
    def total_length(self):
        # raise NotImplementedError()
        return self.get_total_length()

    @property
    def usable_length(self):
        return self.get_usable_length()

    def preGenerateIDs(self, high_value):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()
            if mpi_rank == 0:
                data_package = {}
                data_package["func_name"] = "preGenerateIDs"
                data_package["args"] = 1
                data_package["argv"] = [high_value, ]
                data_package["src_rank"] = mpi_rank
                mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.preGenerateIDs(high_value)

    def enable_id_index_tracking(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "enable_id_index_tracking"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.enable_id_index_tracking()

    def disable_id_index_tracking(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "disable_id_index_tracking"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
        else:
            self._service_process.disable_id_index_tracking()

    def map_id_to_index(self, input_id):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "map_id_to_index"
            data_package["args"] = 1
            data_package["argv"] = [input_id, ]
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
            data = mpi_comm.recv(source=self._serverrank, tag=self._response_tag)
            return int(data["result"])
        else:
            return self._service_process.map_id_to_index(input_id)

    def is_tracking_id_index(self):
        if MPI and self._use_subprocess:
            mpi_comm = MPI.COMM_WORLD
            mpi_rank = mpi_comm.Get_rank()

            data_package = {}
            data_package["func_name"] = "is_tracking_id_index"
            data_package["args"] = 0
            data_package["src_rank"] = mpi_rank
            mpi_comm.send(data_package, dest=self._serverrank, tag=self._request_tag)
            data = mpi_comm.recv(source=self._serverrank, tag=self._response_tag)

            return (True if (data["result"] or data["result"] > 0) else False)
        else:
            return self._service_process.is_tracking_id_index()
