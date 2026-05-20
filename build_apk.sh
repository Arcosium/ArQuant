#!/usr/bin/env bash
# ArQuant Android APK 빌드 — 한 방 자동화 (aarch64 호스트 / amd64 컨테이너)
#
# 사용법:
#   ./build_apk.sh                # debug APK (기본)
#   ./build_apk.sh release        # release APK (서명 필요)
#
# 산출물: /home/opc/projects/ArQuant/ArQuant.apk
set -euo pipefail

HERE="/home/opc/projects/ArQuant"
MOBILE_DIR="${HERE}/arquant_mobile"
IMAGE="arcaive-android-build:jdk17-sdk35"
GRADLE_VOLUME="arcaive-android-gradle-cache"
VARIANT="${1:-debug}"
GRADLE_TASK="assemble$(echo "${VARIANT:0:1}" | tr '[:lower:]' '[:upper:]')${VARIANT:1}"

log(){ printf '\033[1;36m[arquant-apk]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[arquant-apk] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ -d "$MOBILE_DIR" ] || die "모바일 디렉토리 없음: $MOBILE_DIR"
command -v docker >/dev/null || die "docker 가 없습니다."

# QEMU amd64 binfmt 등록 (재부팅마다 1회)
if [ ! -e /proc/sys/fs/binfmt_misc/qemu-x86_64 ]; then
  log "QEMU amd64 핸들러 설치 중…"
  docker run --privileged --rm tonistiigi/binfmt --install amd64 >/dev/null
fi

# 빌드 이미지 존재 확인
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "빌드 이미지 없음: $IMAGE (먼저 /home/opc/android-build/build.sh 로 생성)"

log "Gradle 실행: $GRADLE_TASK"
# 주의: 이미지 entrypoint가 '/bin/bash -lc' 이므로 추가 'bash -c'를 붙이지 말고
# 명령 문자열만 단일 인자로 넘긴다.
docker run --rm --platform linux/amd64 \
  -v "${MOBILE_DIR}:/workspace" \
  -v "${GRADLE_VOLUME}:/root/.gradle" \
  -v "${HERE}:/out" \
  -e GRADLE_USER_HOME=/root/.gradle \
  "$IMAGE" "
    set -e
    echo \"sdk.dir=\${ANDROID_SDK_ROOT}\" > /workspace/local.properties
    chmod +x /workspace/gradlew
    cd /workspace
    ./gradlew --no-daemon ${GRADLE_TASK}
    echo '[arquant-apk] APK 수집…'
    find /workspace -path '*/outputs/apk/${VARIANT}/*' -name '*.apk' -exec cp -v {} /out/ArQuant.apk \\;
  "

ls -lh "${HERE}/ArQuant.apk"
log "완료. 폰에 사이드로드: ArQuant.apk"
