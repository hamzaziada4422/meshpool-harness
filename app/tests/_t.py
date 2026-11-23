from tests.test_fix_cpu_front import *
def test_old(longchats):
    p = longchats
    print("OLDTYPING", p.evaluate("""() => { const i = document.querySelector('#input'); const t0 = performance.now();
      for (let k = 0; k < 20; k++) { i.value += 'a'; i.style.height='auto'; i.style.height = Math.min(260, i.scrollHeight)+'px'; document.body.getBoundingClientRect(); }
      return performance.now() - t0; }"""))
