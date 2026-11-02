"""Coisas do Windows: memória, portas, processos filhos, rede, arquivos completos."""
import ctypes
import glob
import os
import shutil
import socket
import struct
import subprocess
import time

from . import config

log = config.log
WIN = os.name == 'nt'


# ------------------------------------------------------------------ memória

class _MEMSTATUS(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def memoria():
    """(total, disponível) em bytes."""
    if not WIN:
        return (0, 0)
    m = _MEMSTATUS()
    m.dwLength = ctypes.sizeof(m)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return (m.ullTotalPhys, m.ullAvailPhys)
    return (0, 0)


def disco():
    try:
        u = shutil.disk_usage(config.RAIZ)
        return {'total': u.total, 'livre': u.free}
    except OSError:
        return {'total': 0, 'livre': 0}


def nucleos_fisicos():
    """Número de núcleos físicos (para as threads da IA)."""
    n = os.cpu_count() or 4
    if WIN:
        try:
            k = ctypes.windll.kernel32
            tam = ctypes.c_ulong(0)
            k.GetLogicalProcessorInformationEx(0, None, ctypes.byref(tam))  # 0 = RelationProcessorCore
            buf = ctypes.create_string_buffer(tam.value)
            if k.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(tam)):
                cores, off = 0, 0
                while off < tam.value:
                    _, size = struct.unpack_from('<II', buf.raw, off)
                    if size == 0:
                        break
                    cores += 1
                    off += size
                if cores:
                    return cores
        except Exception:
            pass
    return max(1, n // 2)


def nucleos_rapidos():
    """Núcleos de desempenho (P-cores) em processadores híbridos da Intel; nos outros, os físicos.
    A IA fica mais lenta quando mistura núcleos rápidos com os econômicos (E-cores)."""
    if WIN:
        try:
            k = ctypes.windll.kernel32
            tam = ctypes.c_ulong(0)
            k.GetLogicalProcessorInformationEx(0, None, ctypes.byref(tam))
            buf = ctypes.create_string_buffer(tam.value)
            if k.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(tam)):
                classes, off = [], 0
                while off < tam.value:
                    _, size = struct.unpack_from('<II', buf.raw, off)
                    if size == 0:
                        break
                    classes.append(buf.raw[off + 9])   # PROCESSOR_RELATIONSHIP.EfficiencyClass
                    off += size
                if classes:
                    return sum(1 for c in classes if c == max(classes))
        except Exception:
            pass
    return nucleos_fisicos()


# ------------------------------------------------------------------ rede

def porta_livre(inicial, host='127.0.0.1', evitar=()):
    for p in range(inicial, inicial + 60):
        if p in evitar:
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError('nenhuma porta livre a partir de %d' % inicial)


def ips_locais():
    """IPs IPv4 desta máquina. O do Hotspot móvel do Windows costuma ser 192.168.137.1."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.discard('127.0.0.1')
    return sorted(ips, key=lambda ip: (not ip.startswith('192.168.137.'), not ip.startswith('192.168.'), ip))


# ------------------------------------------------------------------ processos filhos

_job = None


def _criar_job():
    """Job Object com KILL_ON_JOB_CLOSE: se este Python morrer (janela fechada), o Windows mata os filhos."""
    global _job
    if not WIN or _job is not None:
        return _job
    k = ctypes.windll.kernel32
    k.CreateJobObjectW.restype = ctypes.c_void_p
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ('a', 'b', 'c', 'd', 'e', 'f')]

    class BASIC(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', ctypes.c_ulong), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', ctypes.c_ulong),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', ctypes.c_ulong),
                    ('SchedulingClass', ctypes.c_ulong)]

    class EXT(ctypes.Structure):
        _fields_ = [('Basic', BASIC), ('Io', IO_COUNTERS), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]

    info = EXT()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        log.warning('SetInformationJobObject falhou')
        return None
    _job = job
    return job


def iniciar_processo(args, cwd=None, log_arquivo=None, env_extra=None):
    """Inicia um programa filho, sem janela, preso ao Job (morre junto com o app)."""
    env = dict(os.environ)
    # temporários dos filhos ficam no SSD, não no C:
    if not config.estado['somente_leitura']:
        env['TEMP'] = env['TMP'] = config.TMP
    if env_extra:
        env.update(env_extra)
    saida = subprocess.DEVNULL
    if log_arquivo and not config.estado['somente_leitura']:
        try:
            saida = open(log_arquivo, 'ab')
        except OSError:
            saida = subprocess.DEVNULL
    flags = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW
    p = subprocess.Popen(args, cwd=cwd, stdout=saida, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=env, creationflags=flags)
    prender_ao_job(p)
    return p


def prender_ao_job(p):
    job = _criar_job()
    if job:
        k = ctypes.windll.kernel32
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not k.AssignProcessToJobObject(job, int(p._handle)):
            log.warning('não consegui prender o processo %s ao job', p.pid)


def encerrar(p, espera=5):
    if p is None or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(espera)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


# ------------------------------------------------------------------ arquivos

def achar(padrao_rel):
    """Glob relativo à raiz do SSD (nomes de pastas variam com a versão)."""
    return sorted(glob.glob(os.path.join(config.RAIZ, padrao_rel), recursive=True))


def zim_completo(caminho):
    """Um ZIM está completo quando tamanho = checksumPos + 16 (o MD5 fica no fim).
    Assim um ZIM ainda sendo baixado (o curl grava direto no nome final) é ignorado."""
    try:
        tam = os.path.getsize(caminho)
        with open(caminho, 'rb') as f:
            cab = f.read(80)
        if len(cab) < 80 or cab[:4] != b'ZIM\x04':
            return False
        checksum = struct.unpack_from('<Q', cab, 72)[0]
        return tam == checksum + 16
    except OSError:
        return False


def arquivo_pronto(caminho, minutos=2):
    """Arquivo sem .ok pode ter sido copiado à mão: aceita se não muda há alguns minutos."""
    try:
        st = os.stat(caminho)
    except OSError:
        return False
    if st.st_size == 0:
        return False
    if os.path.exists(caminho + '.ok'):
        return True
    return (time.time() - st.st_mtime) > minutos * 60


def gguf_valido(caminho):
    try:
        with open(caminho, 'rb') as f:
            return f.read(4) == b'GGUF'
    except OSError:
        return False


def preparar_runtime(exe):
    """O kiwix-serve e o llama-server precisam do runtime do Visual C++ 2022 (msvcp140 >= 14.40). Muitos PCs têm
    um antigo, e aí ele trava ao abrir. Levamos as DLLs em App\\runtime e copiamos para a pasta
    do kiwix-serve (o Windows procura primeiro na pasta do programa)."""
    pasta = os.path.dirname(exe)
    for nome in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
        origem = os.path.join(config.RUNTIME, nome)
        destino = os.path.join(pasta, nome)
        if os.path.exists(origem) and not os.path.exists(destino):
            try:
                shutil.copy2(origem, destino)
                log.info('copiei %s para %s', nome, pasta)
            except OSError as e:
                log.warning('não copiei %s: %s', nome, e)


preparar_runtime_kiwix = preparar_runtime
