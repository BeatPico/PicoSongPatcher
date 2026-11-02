"""The source-only aubio interface used by InfernoSaber, backed by local FFmpeg.

No aubio pitch/tempo APIs are emulated. Upstream's trained feature construction,
normalization, timing decoder and BPM estimator are retained.
"""
import subprocess,types,sys
import numpy as np

def install(ffmpeg):
    class Source:
        def __init__(self,uri,samplerate=0,hop_size=512,channels=1):
            if channels!=1:raise ValueError('Only the mono inference interface is supported')
            self.samplerate=int(samplerate or 44100);self.hop_size=int(hop_size)
            if self.samplerate<=0 or self.hop_size<=0:raise ValueError('Invalid decoder size')
            self.process=subprocess.Popen([str(ffmpeg),'-v','error','-nostdin','-i',str(uri),
                '-vn','-ac','1','-ar',str(self.samplerate),'-f','f32le','pipe:1'],
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.closed=False
        def __call__(self):
            if self.closed:return np.zeros(self.hop_size,dtype=np.float32),0
            raw=self.process.stdout.read(self.hop_size*4)
            if len(raw)%4:raise ValueError('Incomplete audio sample')
            n=len(raw)//4;out=np.zeros(self.hop_size,dtype=np.float32)
            if n:out[:n]=np.frombuffer(raw,dtype='<f4')
            if n<self.hop_size:
                code=self.process.wait()
                self.process.stdout.close();self.closed=True
                if code:raise ValueError('FFmpeg could not decode the copied audio')
            return out,n
        def close(self):
            if not self.closed:
                self.process.stdout.close()
                if self.process.poll() is None:self.process.terminate()
                self.process.wait();self.closed=True
        def __del__(self):
            try:self.close()
            except Exception:pass
    module=types.ModuleType('aubio');module.source=Source
    sys.modules['aubio']=module
    return Source
