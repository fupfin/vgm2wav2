/* vgm2wav2 - batch VGM/S98/DRO/GYM to WAV converter
 * Supports: single file, directory (recursive), zip archive
 * Based on libvgm's vgm2wav.cpp
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>
#include <filesystem>
#include <zip.h>

#include "player/playerbase.hpp"
#include "player/vgmplayer.hpp"
#include "player/s98player.hpp"
#include "player/droplayer.hpp"
#include "player/gymplayer.hpp"
#include "player/playera.hpp"
#include "utils/DataLoader.h"
#include "utils/FileLoader.h"
#include "utils/MemoryLoader.h"
#include "emu/SoundDevs.h"
#include "emu/EmuCores.h"

#ifdef _MSC_VER
#define strncasecmp _strnicmp
#define snprintf    _snprintf
#endif

namespace fs = std::filesystem;

#define BUFFER_LEN 2048

static double        fade_len    = 8.0;
static unsigned int  sample_rate = 44100;
static unsigned int  bit_depth   = 16;
static unsigned int  loops       = 2;

static const char *extensible_guid_trailer =
    "\x00\x00\x00\x00\x10\x00\x80\x00\x00\xAA\x00\x38\x9B\x71";

/* ---- helpers (same as vgm2wav.cpp) ---- */

static void pack_uint16le(UINT8 *d, UINT16 n) {
    d[0] = (UINT8)((UINT16)n     );
    d[1] = (UINT8)((UINT16)n >> 8);
}

static void pack_uint32le(UINT8 *d, UINT32 n) {
    d[0] = (UINT8)(n      );
    d[1] = (UINT8)(n >>  8);
    d[2] = (UINT8)(n >> 16);
    d[3] = (UINT8)(n >> 24);
}

static inline void repack_int16le(UINT8 *d, const UINT8 *src) {
#ifdef VGM_BIG_ENDIAN
    UINT8 tmp[2]; memcpy(tmp,src,2); d[0]=tmp[1]; d[1]=tmp[0];
#else
    (void)d; (void)src;
#endif
}

static inline void repack_int24le(UINT8 *d, const UINT8 *src) {
#ifdef VGM_BIG_ENDIAN
    UINT8 tmp[3]; memcpy(tmp,src,3); d[0]=tmp[2]; d[1]=tmp[1]; d[2]=tmp[0];
#else
    (void)d; (void)src;
#endif
}

static inline void repack_int32le(UINT8 *d, const UINT8 *src) {
#ifdef VGM_BIG_ENDIAN
    UINT8 tmp[4]; memcpy(tmp,src,4); d[0]=tmp[3]; d[1]=tmp[2]; d[2]=tmp[1]; d[3]=tmp[0];
#else
    (void)d; (void)src;
#endif
}

static int write_wav_header(FILE *f, unsigned int totalFrames) {
    unsigned int dataSize = totalFrames * (bit_depth / 8) * 2;
    UINT8 tmp[4];
    if(fwrite("RIFF",1,4,f) != 4) return 0;
    pack_uint32le(tmp, 4 + (8 + dataSize) + (8 + 40));
    if(fwrite(tmp,1,4,f) != 4) return 0;
    if(fwrite("WAVE",1,4,f) != 4) return 0;
    if(fwrite("fmt ",1,4,f) != 4) return 0;
    pack_uint32le(tmp,40);
    if(fwrite(tmp,1,4,f) != 4) return 0;
    pack_uint16le(tmp,0xFFFE);
    if(fwrite(tmp,1,2,f) != 2) return 0;
    pack_uint16le(tmp,2);
    if(fwrite(tmp,1,2,f) != 2) return 0;
    pack_uint32le(tmp,sample_rate);
    if(fwrite(tmp,1,4,f) != 4) return 0;
    pack_uint32le(tmp,sample_rate * 2 * (bit_depth / 8));
    if(fwrite(tmp,1,4,f) != 4) return 0;
    pack_uint16le(tmp,2 * (bit_depth / 8));
    if(fwrite(tmp,1,2,f) != 2) return 0;
    pack_uint16le(tmp,bit_depth);
    if(fwrite(tmp,1,2,f) != 2) return 0;
    pack_uint16le(tmp,22);
    if(fwrite(tmp,1,2,f) != 2) return 0;
    pack_uint16le(tmp,bit_depth);
    if(fwrite(tmp,1,2,f) != 2) return 0;
    pack_uint32le(tmp,3);
    if(fwrite(tmp,1,4,f) != 4) return 0;
    pack_uint16le(tmp,1);
    if(fwrite(tmp,1,2,f) != 2) return 0;
    if(fwrite(extensible_guid_trailer,1,14,f) != 14) return 0;
    if(fwrite("data",1,4,f) != 4) return 0;
    pack_uint32le(tmp,dataSize);
    if(fwrite(tmp,1,4,f) != 4) return 0;
    return 1;
}

static void frames_to_little_endian(UINT8 *data, unsigned int frame_count) {
    unsigned int i = 0;
    while(i < frame_count) {
        switch(bit_depth) {
            case 32: repack_int32le(&data[0],&data[0]); repack_int32le(&data[4],&data[4]); break;
            case 24: repack_int24le(&data[0],&data[0]); repack_int24le(&data[3],&data[3]); break;
            default: repack_int16le(&data[0],&data[0]); repack_int16le(&data[2],&data[2]); break;
        }
        i++;
        data += (bit_depth / 8) * 2;
    }
}

/* ---- core render function ---- */

static int render_to_wav(DATA_LOADER *loader, const char *in_name, const char *out_path) {
    PlayerA player;

    UINT8 *packed = (UINT8 *)malloc(sizeof(INT32) * 2 * BUFFER_LEN);
    if(!packed) { fprintf(stderr, "out of memory\n"); return 1; }

    player.RegisterPlayerEngine(new VGMPlayer);
    player.RegisterPlayerEngine(new S98Player);
    player.RegisterPlayerEngine(new DROPlayer);
    player.RegisterPlayerEngine(new GYMPlayer);

    if(player.SetOutputSettings(sample_rate, 2, bit_depth, BUFFER_LEN)) {
        fprintf(stderr, "Unsupported sample rate / bps\n");
        free(packed);
        return 1;
    }

    {
        PlayerA::Config pCfg = player.GetConfiguration();
        pCfg.masterVol = 0x10000;
        pCfg.loopCount = loops;
        pCfg.fadeSmpls = (UINT32)(sample_rate * fade_len);
        pCfg.endSilenceSmpls = 0;
        pCfg.pbSpeed = 1.0;
        player.SetConfiguration(pCfg);
    }

    DataLoader_SetPreloadBytes(loader, 0x100);
    if(DataLoader_Load(loader)) {
        fprintf(stderr, "Failed to load: %s\n", in_name);
        free(packed);
        return 1;
    }

    if(player.LoadFile(loader)) {
        fprintf(stderr, "Failed to parse: %s\n", in_name);
        free(packed);
        return 1;
    }

    PlayerBase *plrEngine = player.GetPlayer();
    if(plrEngine->GetPlayerType() == FCC_VGM) {
        VGMPlayer *vgmplay = dynamic_cast<VGMPlayer *>(plrEngine);
        player.SetLoopCount(vgmplay->GetModifiedLoopCount(loops));
    }

    FILE *f = fopen(out_path, "wb");
    if(!f) {
        fprintf(stderr, "Cannot open output: %s\n", out_path);
        free(packed);
        return 1;
    }

    player.Start();

    unsigned int totalFrames = plrEngine->Tick2Sample(plrEngine->GetTotalPlayTicks(loops));
    if(plrEngine->GetLoopTicks() > 0)
        totalFrames += player.GetFadeSamples();

    fprintf(stderr, "%s -> %s  [%.1fs]\n",
        in_name, out_path,
        plrEngine->Sample2Second(totalFrames));

    write_wav_header(f, totalFrames);

    double complete = 0.0;
    double inc = (totalFrames > 0) ? (double)BUFFER_LEN / totalFrames : 1.0;
    fprintf(stderr, "[");
    fflush(stderr);

    while(totalFrames) {
        memset(packed, 0, sizeof(INT32) * BUFFER_LEN * 2);
        unsigned int curFrames = (BUFFER_LEN > totalFrames ? totalFrames : BUFFER_LEN);
        player.Render(curFrames * ((bit_depth / 8) * 2), packed);
        frames_to_little_endian(packed, curFrames);
        fwrite(packed, (bit_depth / 8) * 2, curFrames, f);
        totalFrames -= curFrames;
        complete += inc;
        if(complete >= 0.10) { complete -= 0.10; fprintf(stderr, "-"); fflush(stderr); }
    }
    fprintf(stderr, "]\n");

    player.Stop();
    player.UnloadFile();
    player.UnregisterAllPlayers();
    free(packed);
    fclose(f);
    return 0;
}

/* ---- input type helpers ---- */

static bool is_vgm_ext(const fs::path &p) {
    std::string ext = p.extension().string();
    for(auto &c : ext) c = (char)tolower((unsigned char)c);
    return ext == ".vgm" || ext == ".vgz" || ext == ".s98"
        || ext == ".dro" || ext == ".gym";
}

static int process_file(const std::string &in_path, const std::string &out_path) {
    DATA_LOADER *loader = FileLoader_Init(in_path.c_str());
    if(!loader) { fprintf(stderr, "Failed to open: %s\n", in_path.c_str()); return 1; }
    int ret = render_to_wav(loader, in_path.c_str(), out_path.c_str());
    DataLoader_Deinit(loader);
    return ret;
}

static int process_directory(const std::string &in_dir, const std::string &out_dir) {
    fs::create_directories(out_dir);
    int errors = 0;
    for(auto &entry : fs::recursive_directory_iterator(in_dir)) {
        if(!entry.is_regular_file() || !is_vgm_ext(entry.path())) continue;
        fs::path rel     = fs::relative(entry.path(), in_dir);
        fs::path out     = fs::path(out_dir) / rel;
        out.replace_extension(".wav");
        fs::create_directories(out.parent_path());
        errors += process_file(entry.path().string(), out.string());
    }
    return errors;
}

static int process_zip(const std::string &zip_path, const std::string &out_dir) {
    int err = 0;
    zip_t *za = zip_open(zip_path.c_str(), ZIP_RDONLY, &err);
    if(!za) {
        fprintf(stderr, "Failed to open zip: %s (err=%d)\n", zip_path.c_str(), err);
        return 1;
    }
    fs::create_directories(out_dir);
    int errors = 0;
    zip_int64_t n = zip_get_num_entries(za, 0);

    for(zip_int64_t i = 0; i < n; i++) {
        zip_stat_t st;
        if(zip_stat_index(za, i, 0, &st) != 0) continue;
        if(!(st.valid & ZIP_STAT_NAME) || !(st.valid & ZIP_STAT_SIZE)) continue;

        fs::path entry_path(st.name);
        if(!is_vgm_ext(entry_path)) continue;

        zip_file_t *zf = zip_fopen_index(za, i, 0);
        if(!zf) { errors++; continue; }

        std::vector<UINT8> buf((size_t)st.size);
        if(zip_fread(zf, buf.data(), st.size) != (zip_int64_t)st.size) {
            fprintf(stderr, "Read error in zip entry: %s\n", st.name);
            zip_fclose(zf);
            errors++;
            continue;
        }
        zip_fclose(zf);

        DATA_LOADER *loader = MemoryLoader_Init(buf.data(), (UINT32)buf.size());
        if(!loader) { errors++; continue; }

        fs::path out = fs::path(out_dir) / entry_path;
        out.replace_extension(".wav");
        fs::create_directories(out.parent_path());

        errors += render_to_wav(loader, st.name, out.string().c_str());
        DataLoader_Deinit(loader);
    }
    zip_close(za);
    return errors;
}

/* ---- main ---- */

static unsigned int scan_uint(const char *str) {
    unsigned int num = 0;
    while(*str >= '0' && *str <= '9') { num = num * 10 + (*str - '0'); str++; }
    return num;
}

#define str_equals(s1,s2)    (strcmp(s1,s2) == 0)
#define str_istarts(s1,s2)   (strncasecmp(s1,s2,strlen(s2)) == 0)

int main(int argc, const char *argv[]) {
    const char *self = *argv++;
    argc--;

    while(argc > 0) {
        const char *arg = *argv;
        if(str_equals(arg, "--")) { argv++; argc--; break; }

        const char *val = NULL;
        const char *eq  = strchr(arg, '=');

        auto next_val = [&]() -> const char * {
            if(eq) return eq + 1;
            if(argc > 1) { argv++; argc--; return *argv; }
            return NULL;
        };

        if(str_istarts(arg, "--loops"))      { val = next_val(); if(val) loops       = scan_uint(val); }
        else if(str_istarts(arg, "--samplerate")) { val = next_val(); if(val) sample_rate = scan_uint(val); }
        else if(str_istarts(arg, "--bps"))   { val = next_val(); if(val) bit_depth   = scan_uint(val); }
        else if(str_istarts(arg, "--fade"))  { val = next_val(); if(val) fade_len    = strtod(val, NULL); }
        else break;

        argv++; argc--;
    }

    if(loops == 0)       loops       = 2;
    if(sample_rate == 0) sample_rate = 44100;
    if(bit_depth != 16 && bit_depth != 24 && bit_depth != 32) bit_depth = 16;

    if(argc < 2) {
        fprintf(stderr,
            "Usage: %s [options] <input> <output>\n"
            "  input   : VGM/VGZ/S98/DRO/GYM file, directory, or .zip archive\n"
            "  output  : WAV file (single input) or directory (folder/zip input)\n"
            "Options:\n"
            "  --samplerate n  (default: 44100)\n"
            "  --bps n         16/24/32 (default: 16)\n"
            "  --fade x        fade-out seconds (default: 8.0)\n"
            "  --loops n       loop count (default: 2)\n",
            self);
        return 1;
    }

    fs::path in(argv[0]);
    std::string out(argv[1]);

    if(fs::is_directory(in)) {
        return process_directory(in.string(), out);
    } else {
        std::string ext = in.extension().string();
        for(auto &c : ext) c = (char)tolower((unsigned char)c);
        if(ext == ".zip") {
            return process_zip(in.string(), out);
        } else {
            return process_file(in.string(), out);
        }
    }
}
