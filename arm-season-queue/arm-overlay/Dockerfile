FROM arm-vision-arm:19.1.0-f6ec2e3
ARG SOURCE_COMMIT=unknown
ARG IMAGE_VERSION=local
COPY apply_path_fix.py /tmp/apply_path_fix.py
RUN python3 /tmp/apply_path_fix.py /opt/arm \
    && rm /tmp/apply_path_fix.py
LABEL org.opencontainers.image.source="https://github.com/TammuzGreeen/ARM-ripper-vision" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.base.name="arm-vision-arm:19.1.0-f6ec2e3"
