//! Streaming three-color van der Waerden run scanner (Rust port).
//!
//! For a prime p ≡ 1 (mod 3) this computes the cubic power-residue coloring of
//! [1, p-1] on the fly -- class(n) = which cube root of unity n^((p-1)/3) mod p
//! lands on, using the canonical *sorted* root labels {1, ζ, ζ²} -- and reports
//! the longest monochromatic run, the leading run, and the colors of 1 and p-1.
//! Those four numbers are everything vdw_reach.py needs to decide which W(3,t)
//! record cells the prime beats (bound (t-1)p+1) and to check the boundary rule.
//!
//! This is the constant-memory streaming path only: no O(p) coloring array is
//! ever built, so p is bounded by time, not RAM. Work is split into contiguous
//! n-ranges scanned in parallel (rayon); each range returns a run *summary*
//! (leading run, trailing run, interior max, whether it aborted) and the
//! summaries combine with an associative monoid, so the parallel result is
//! bit-identical to a serial left-to-right scan regardless of chunking.
//!
//! The labeling is canonical (roots sorted by value, independent of any
//! generator), so vdw_reach.py's independent re-verification varies the CHUNK
//! size instead of the generator: a different chunk boundary independently
//! exercises the run-carry stitching, which is the only nontrivial logic here.
//!
//! Usage:  vdw_scan <p> [--chunk N]
//! Output: one line "max_run lead first last" (space-separated integers).
//!         On early abort (a run reaches ABORT_RUN) it prints ABORT_RUN and
//!         zeros -- such a prime improves no cell in range, so the exact
//!         leading/first/last are irrelevant and not computed.

use std::env;
use std::process;
use std::sync::atomic::{AtomicBool, Ordering};

use rayon::prelude::*;

const R: u64 = 3;
/// A run this long is useless for every cell t ≤ 25 (needs t ≥ 26), so the scan
/// short-circuits the instant any range reaches it. Mirrors vdw_reach.ABORT_RUN.
const ABORT_RUN: u64 = 25;
const DEFAULT_CHUNK: u64 = 1 << 20;

static ABORT: AtomicBool = AtomicBool::new(false);

/// Montgomery arithmetic mod an odd modulus n < 2^64 (every prime p ≡ 1 mod 3,
/// p ≥ 5, is odd). The per-element cost here is one modular exponentiation, so
/// the modmul must be division-free: `n^e mod p` via `u128 %` ties numpy's
/// Barrett trick, whereas Montgomery REDC (a multiply, a shift, one conditional
/// subtract -- no division) is several times faster. `to`/`from` move a value
/// in and out of the Montgomery domain (a·R mod n, with R = 2^64).
struct Mont {
    n: u64,
    ninv: u64, // -n^{-1} mod 2^64
    r: u64,    // R mod n   == Montgomery form of 1
    r2: u64,   // R^2 mod n == 2^128 mod n
}

impl Mont {
    fn new(n: u64) -> Mont {
        // n^{-1} mod 2^64 by Newton iteration (doubles correct bits each step:
        // 1 -> 2 -> 4 -> ... -> 64), then negate for -n^{-1}.
        let mut inv = 1u64;
        for _ in 0..6 {
            inv = inv.wrapping_mul(2u64.wrapping_sub(n.wrapping_mul(inv)));
        }
        let r = ((1u128 << 64) % n as u128) as u64; // 2^64 mod n
        let r2 = ((r as u128 * r as u128) % n as u128) as u64; // 2^128 mod n
        Mont {
            n,
            ninv: inv.wrapping_neg(),
            r,
            r2,
        }
    }

    /// REDC(a·b): given a,b in Montgomery form, returns a·b·R^{-1} mod n, i.e.
    /// the Montgomery form of the product. Division-free.
    #[inline(always)]
    fn mul(&self, a: u64, b: u64) -> u64 {
        let t = a as u128 * b as u128;
        let m = (t as u64).wrapping_mul(self.ninv);
        let u = ((t + m as u128 * self.n as u128) >> 64) as u64;
        if u >= self.n {
            u - self.n
        } else {
            u
        }
    }

    #[inline(always)]
    fn to(&self, a: u64) -> u64 {
        self.mul(a % self.n, self.r2)
    }

    #[inline(always)]
    fn from(&self, a: u64) -> u64 {
        self.mul(a, 1)
    }

    /// base^e mod n, returned as an ordinary residue (not Montgomery form).
    #[inline]
    fn pow(&self, base: u64, mut e: u64) -> u64 {
        let mut a = self.to(base);
        let mut acc = self.r; // Montgomery form of 1
        while e > 0 {
            if e & 1 == 1 {
                acc = self.mul(acc, a);
            }
            a = self.mul(a, a);
            e >>= 1;
        }
        self.from(acc)
    }

    /// base[i]^e mod n for four bases at once, all with the SAME exponent e.
    /// A single modpow is a dependency chain (each square waits on the last),
    /// so one element leaves the multiplier mostly idle -- latency-bound. Since
    /// e is shared, all four lanes take the identical square/multiply schedule
    /// with no per-lane branching, so the four independent REDCs at each step
    /// pipeline through the out-of-order core and hide each other's latency.
    /// This is the main single-core throughput lever (~2-3x over `pow`).
    #[inline]
    fn pow4(&self, base: [u64; 4], mut e: u64) -> [u64; 4] {
        let mut a = [
            self.to(base[0]),
            self.to(base[1]),
            self.to(base[2]),
            self.to(base[3]),
        ];
        let mut acc = [self.r; 4];
        while e > 0 {
            if e & 1 == 1 {
                for i in 0..4 {
                    acc[i] = self.mul(acc[i], a[i]);
                }
            }
            for i in 0..4 {
                a[i] = self.mul(a[i], a[i]);
            }
            e >>= 1;
        }
        [
            self.from(acc[0]),
            self.from(acc[1]),
            self.from(acc[2]),
            self.from(acc[3]),
        ]
    }
}

/// The three cube roots of unity mod p, sorted ascending. Found without a full
/// primitive-root search: any a with a^((p-1)/3) ≠ 1 yields an order-3 element
/// ζ, and the set {1, ζ, ζ²} is the complete root set regardless of which a --
/// so sorting gives the same canonical labels vdw_reach.py's stream path uses.
fn cube_roots(m: &Mont, e: u64) -> [u64; 3] {
    let mut a = 2u64;
    let zeta = loop {
        let z = m.pow(a, e);
        if z != 1 {
            break z;
        }
        a += 1;
    };
    let mut roots = [1u64, zeta, ((zeta as u128 * zeta as u128) % m.n as u128) as u64];
    roots.sort_unstable();
    roots
}

#[inline(always)]
fn label(x: u64, roots: &[u64; 3]) -> u8 {
    // Exactly one of the three sorted roots equals x (n^e is always a cube root
    // of unity here). Linear match over 3 elements; index is the class label.
    if x == roots[0] {
        0
    } else if x == roots[1] {
        1
    } else {
        2
    }
}

#[inline(always)]
fn class_of(n: u64, m: &Mont, e: u64, roots: &[u64; 3]) -> u8 {
    label(m.pow(n, e), roots)
}

#[inline(always)]
fn class4(n: u64, m: &Mont, e: u64, roots: &[u64; 3]) -> [u8; 4] {
    let xs = m.pow4([n, n + 1, n + 2, n + 3], e);
    [
        label(xs[0], roots),
        label(xs[1], roots),
        label(xs[2], roots),
        label(xs[3], roots),
    ]
}

/// Sequential longest-run / leading-run tracker fed one color at a time. The
/// run logic is inherently sequential but cheap; the expensive modpow is what
/// gets batched (class4). `push` returns true the instant a run reaches
/// ABORT_RUN, so the caller can bail.
struct RunState {
    cur_c: u8,
    cur_len: u64,
    pre_len: u64,
    pre_open: bool,
    max_run: u64,
    last_c: u8,
}

impl RunState {
    #[inline]
    fn new(first: u8) -> RunState {
        RunState {
            cur_c: first,
            cur_len: 1,
            pre_len: 1,
            pre_open: true,
            max_run: 1,
            last_c: first,
        }
    }

    #[inline(always)]
    fn push(&mut self, c: u8) -> bool {
        if c == self.cur_c {
            self.cur_len += 1;
            if self.cur_len > self.max_run {
                self.max_run = self.cur_len;
                if self.max_run >= ABORT_RUN {
                    return true;
                }
            }
        } else {
            self.pre_open = false;
            self.cur_c = c;
            self.cur_len = 1;
        }
        if self.pre_open {
            self.pre_len = self.cur_len;
        }
        self.last_c = c;
        false
    }
}

/// Run summary for a contiguous slice of the color sequence. `len == 0` is the
/// monoid identity. Combining is associative, so any chunking reproduces the
/// serial scan. On abort, `aborted` is set and the numeric fields are ignored.
#[derive(Clone, Copy)]
struct Seg {
    len: u64,
    pre_c: u8,
    pre_len: u64,
    suf_c: u8,
    suf_len: u64,
    max_run: u64,
    aborted: bool,
}

impl Seg {
    const ID: Seg = Seg {
        len: 0,
        pre_c: 0,
        pre_len: 0,
        suf_c: 0,
        suf_len: 0,
        max_run: 0,
        aborted: false,
    };
}

/// Scan n in [lo, hi) sequentially, returning its run summary. Sets/observes the
/// global ABORT flag so that once any range hits ABORT_RUN every range bails.
fn scan_range(lo: u64, hi: u64, m: &Mont, e: u64, roots: &[u64; 3]) -> Seg {
    if ABORT.load(Ordering::Relaxed) {
        return Seg {
            aborted: true,
            ..Seg::ID
        };
    }
    let first = class_of(lo, m, e, roots);
    let mut rs = RunState::new(first);
    let aborted_seg = Seg {
        aborted: true,
        ..Seg::ID
    };

    let mut n = lo + 1;
    // Batch four modpows at a time (ILP); scalar tail handles the last < 4.
    while n + 4 <= hi {
        // Cheap periodic check so a sibling range's abort stops this one too.
        if (n & 0xFFFF) < 4 && ABORT.load(Ordering::Relaxed) {
            return aborted_seg;
        }
        for c in class4(n, m, e, roots) {
            if rs.push(c) {
                ABORT.store(true, Ordering::Relaxed);
                return aborted_seg;
            }
        }
        n += 4;
    }
    while n < hi {
        if rs.push(class_of(n, m, e, roots)) {
            ABORT.store(true, Ordering::Relaxed);
            return aborted_seg;
        }
        n += 1;
    }

    Seg {
        len: hi - lo,
        pre_c: first,
        pre_len: rs.pre_len,
        suf_c: rs.last_c,
        suf_len: rs.cur_len,
        max_run: rs.max_run,
        aborted: false,
    }
}

fn combine(a: Seg, b: Seg) -> Seg {
    if a.aborted || b.aborted {
        return Seg {
            aborted: true,
            ..Seg::ID
        };
    }
    if a.len == 0 {
        return b;
    }
    if b.len == 0 {
        return a;
    }
    let cross = if a.suf_c == b.pre_c {
        a.suf_len + b.pre_len
    } else {
        0
    };
    let max_run = a.max_run.max(b.max_run).max(cross);

    // combined leading run: extends into b only if a is entirely one color and
    // that color matches b's leading color.
    let (pre_c, pre_len) = if a.pre_len == a.len && a.pre_c == b.pre_c {
        (a.pre_c, a.len + b.pre_len)
    } else {
        (a.pre_c, a.pre_len)
    };
    // combined trailing run: symmetric.
    let (suf_c, suf_len) = if b.suf_len == b.len && b.suf_c == a.suf_c {
        (b.suf_c, b.len + a.suf_len)
    } else {
        (b.suf_c, b.suf_len)
    };

    Seg {
        len: a.len + b.len,
        pre_c,
        pre_len,
        suf_c,
        suf_len,
        max_run,
        aborted: false,
    }
}

fn scan(p: u64, chunk: u64) -> (u64, u64, u8, u8) {
    let e = (p - 1) / R;
    let m = Mont::new(p);
    let roots = cube_roots(&m, e);
    ABORT.store(false, Ordering::Relaxed);

    // Chunk boundaries over the color domain n = 1 ..= p-1.
    let starts: Vec<u64> = (1..p).step_by(chunk as usize).collect();
    let total = combine_all(&starts, p, chunk, &m, e, &roots);

    if total.aborted {
        // A range hit ABORT_RUN and bailed before recording its ends; such a
        // prime beats no cell, so leading/first/last are moot. Mirror Python's
        // capped return.
        return (ABORT_RUN, 0, 0, 0);
    }
    // A run can also cross a chunk seam without any single range aborting; cap
    // it the same way. Real leading run / end colors are still known here.
    let mr = total.max_run.min(ABORT_RUN);
    (mr, total.pre_len, total.pre_c, total.suf_c)
}

fn combine_all(starts: &[u64], p: u64, chunk: u64, m: &Mont, e: u64, roots: &[u64; 3]) -> Seg {
    starts
        .par_iter()
        .map(|&lo| {
            let hi = (lo + chunk).min(p);
            scan_range(lo, hi, m, e, roots)
        })
        .reduce(|| Seg::ID, combine)
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: vdw_scan <p> [--chunk N]");
        process::exit(2);
    }
    let p: u64 = args[1].parse().unwrap_or_else(|_| {
        eprintln!("bad prime: {}", args[1]);
        process::exit(2);
    });
    let mut chunk = DEFAULT_CHUNK;
    let mut i = 2;
    while i < args.len() {
        if args[i] == "--chunk" && i + 1 < args.len() {
            chunk = args[i + 1].parse().unwrap_or(DEFAULT_CHUNK);
            i += 2;
        } else {
            eprintln!("unknown arg: {}", args[i]);
            process::exit(2);
        }
    }
    if p < 5 || p % R != 1 {
        eprintln!("p must be a prime ≡ 1 (mod 3), got {}", p);
        process::exit(2);
    }
    let chunk = chunk.max(1);

    let (mr, lead, first, last) = scan(p, chunk);
    println!("{} {} {} {}", mr, lead, first, last);
}
