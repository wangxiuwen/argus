APP     = Mira
VERSION = 0.4.8
OUT     = .build/$(APP)
BUNDLE  = $(HOME)/Applications/$(APP).app
BIN_DIR = $(HOME)/.local/bin
STAGE   = .build/stage
DIST    = dist/mira-$(VERSION)-macos-arm64.tar.gz
SHARE_FILES = share/chat.py share/ui.py share/ui.html share/settings.html share/MiraIcon.png \
	share/launch.py share/bridge.py share/prune.py share/video.py share/music.py \
	share/image.py share/jobs.py share/iteration.py
VIDEO_PIPELINES = share/video-pipelines/*.vpipeline
ICON = assets/Mira.icns
VPIPE_DMG = .cache/VpipeManager-0.1.26-with-ffmpeg.dmg
VPIPE_SHA256 = 25abea40b3bde34670295939771f288832f5b5358374140fce0c20bdc177b276
MLX_SERVE_ARCHIVE = .cache/mlx-serve-bin-macos-arm64-26.8.8.tar.gz
MLX_SERVE_SHA256 = 15a083189124f67b1625fc4c2f76726fa6b82c052812c398ad5c3e250386fc0a

.PHONY: build test install uninstall clean dist verify-vpipe verify-mlx-serve

build:
	mkdir -p .build
	swiftc -O -o $(OUT) Mira/main.swift
	lipo $(OUT) -verify_arch arm64

test:
	python3 -m unittest discover -s tests -v
	python3 -m py_compile share/*.py
	zsh -n bin/argus
	sh -n install.sh

verify-vpipe: $(VPIPE_DMG)
	@echo "$(VPIPE_SHA256)  $(VPIPE_DMG)" | shasum -a 256 -c -

$(VPIPE_DMG):
	mkdir -p .cache
	curl -fL --retry 3 -o "$@" https://github.com/tgo-app-dev/vpipe/releases/download/v0.1.26/VpipeManager-0.1.26-with-ffmpeg.dmg

verify-mlx-serve: $(MLX_SERVE_ARCHIVE)
	@echo "$(MLX_SERVE_SHA256)  $(MLX_SERVE_ARCHIVE)" | shasum -a 256 -c -

$(MLX_SERVE_ARCHIVE):
	mkdir -p .cache
	curl -fL --retry 3 -o "$@" https://github.com/ddalcu/mlx-serve/releases/download/v26.8.8/mlx-serve-bin-macos-arm64.tar.gz

install: build verify-vpipe verify-mlx-serve
	mkdir -p $(BUNDLE)/Contents/MacOS $(BUNDLE)/Contents/Resources/video-pipelines $(BIN_DIR)
	cp $(OUT) $(BUNDLE)/Contents/MacOS/
	cp Info.plist $(BUNDLE)/Contents/
	cp $(VIDEO_PIPELINES) $(BUNDLE)/Contents/Resources/video-pipelines/
	cp $(ICON) $(BUNDLE)/Contents/Resources/Mira.icns
	./scripts/embed-vpipe.sh $(VPIPE_DMG) $(BUNDLE)
	./scripts/embed-mlx-serve.sh $(MLX_SERVE_ARCHIVE) $(BUNDLE)
	plutil -replace CFBundleShortVersionString -string $(VERSION) $(BUNDLE)/Contents/Info.plist
	plutil -replace CFBundleVersion -string $(VERSION) $(BUNDLE)/Contents/Info.plist
	codesign --force --deep --sign - $(BUNDLE)
	install -m 755 bin/argus $(BIN_DIR)/argus
	install -m 755 bin/argus $(BIN_DIR)/mira
	mkdir -p $(HOME)/.local/share/argus
	install -m 644 $(SHARE_FILES) $(HOME)/.local/share/argus/
	@echo "installed: $(BUNDLE) and $(BIN_DIR)/mira (argus compatibility alias retained)"
	@echo "open the tray app with: open $(BUNDLE)"

uninstall:
	-$(BIN_DIR)/argus bridge stop
	-$(BIN_DIR)/argus ui stop
	-$(BIN_DIR)/argus stop
	rm -rf $(BUNDLE)
	rm -f $(BIN_DIR)/argus
	rm -f $(BIN_DIR)/mira
	rm -rf $(HOME)/.local/share/argus

dist: build verify-vpipe verify-mlx-serve
	rm -rf $(STAGE) && mkdir -p $(STAGE)/$(APP).app/Contents/MacOS $(STAGE)/$(APP).app/Contents/Resources/video-pipelines $(STAGE)/bin $(STAGE)/share/video-pipelines dist
	cp $(OUT) $(STAGE)/$(APP).app/Contents/MacOS/
	cp Info.plist $(STAGE)/$(APP).app/Contents/
	cp $(VIDEO_PIPELINES) $(STAGE)/$(APP).app/Contents/Resources/video-pipelines/
	cp $(ICON) $(STAGE)/$(APP).app/Contents/Resources/Mira.icns
	./scripts/embed-vpipe.sh $(VPIPE_DMG) $(STAGE)/$(APP).app
	./scripts/embed-mlx-serve.sh $(MLX_SERVE_ARCHIVE) $(STAGE)/$(APP).app
	plutil -replace CFBundleShortVersionString -string $(VERSION) $(STAGE)/$(APP).app/Contents/Info.plist
	plutil -replace CFBundleVersion -string $(VERSION) $(STAGE)/$(APP).app/Contents/Info.plist
	codesign --force --deep --sign - $(STAGE)/$(APP).app
	cp bin/argus $(STAGE)/bin/
	cp $(SHARE_FILES) $(STAGE)/share/
	cp $(VIDEO_PIPELINES) $(STAGE)/share/video-pipelines/
	cp install.sh README.md LICENSE THIRD_PARTY_NOTICES.md $(STAGE)/
	cp -R LICENSES $(STAGE)/
	chmod +x $(STAGE)/install.sh $(STAGE)/bin/argus
	tar -czf $(DIST) -C $(STAGE) .
	@echo "built $(DIST)"

clean:
	rm -rf .build dist
