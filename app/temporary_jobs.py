"""Short-lived document downloads in RAM; never persist student certificates."""
from collections import OrderedDict
from threading import RLock, Timer
from time import time


LIFETIME_SECONDS = 30 * 60


class TemporaryJobs:
    def __init__(self, lifetime=LIFETIME_SECONDS, max_bytes=64_000_000, max_jobs=16):
        self.lifetime, self.max_bytes, self.max_jobs = lifetime, max_bytes, max_jobs
        self.jobs = OrderedDict()
        self.lock = RLock()

    def delete(self, key):
        with self.lock:
            job = self.jobs.pop(key, None)
            if job:
                job['timer'].cancel()

    def put(self, key, files, filename):
        size = sum(map(len, files.values()))
        if size > self.max_bytes:
            raise ValueError('El lote ocupa demasiado espacio temporal. Divide los alumnos en lotes más pequeños.')
        with self.lock:
            while self.jobs and (len(self.jobs) >= self.max_jobs or sum(j['size'] for j in self.jobs.values()) + size > self.max_bytes):
                self.delete(next(iter(self.jobs)))
            timer = Timer(self.lifetime, self.delete, args=(key,))
            timer.daemon = True
            self.jobs[key] = {'files': files, 'filename': filename, 'size': size, 'expires': time() + self.lifetime, 'timer': timer}
            timer.start()

    def get(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if job and job['expires'] <= time():
                self.delete(key)
                return None
            return job


temporary_jobs = TemporaryJobs()
