#import "gw.h"

#pragma mark - hàng mô tả một model

static NSString *healthWord(NSString *h) {
    if ([h isEqualToString:@"ok"]) return @"tốt";
    if ([h isEqualToString:@"fail"]) return @"lỗi";
    return @"chưa kiểm";
}

GWRow *gwModelRow(NSDictionary *m) {
    NSString *mid = m[@"id"] ?: @"";
    NSString *name = m[@"display_name"];
    if (![name isKindOfClass:[NSString class]] || name.length == 0) name = mid;

    NSMutableString *sub = [NSMutableString stringWithFormat:@"%@ · %@",
                            gwProviderLabel(m[@"provider"] ?: @""), healthWord(m[@"health"])];
    if ([m[@"is_free"] integerValue] == 1) [sub appendString:@" · miễn phí"];
    if ([m[@"available"] integerValue] == 0) [sub appendString:@" · máy chủ không có"];
    double last = [m[@"last_test_at"] doubleValue];
    if (last > 0) [sub appendFormat:@" · kiểm %@", gwAgo(last)];

    GWRow *r = [GWRow title:name sub:sub];
    r.hasSwitch = YES;
    r.switchOn = [m[@"enabled"] integerValue] == 1;
    if ([m[@"health"] isEqualToString:@"ok"]) r.dotKind = 1;
    else if ([m[@"health"] isEqualToString:@"fail"]) r.dotKind = 2;
    else r.dotKind = 4;
    r.extra = m;
    return r;
}

void gwModelSwitch(GWTableVC *vc, GWRow *row, NSDictionary *m, void (^after)(void)) {
    NSString *mid = m[@"id"];
    if (mid.length == 0) return;
    NSString *enc = [mid stringByAddingPercentEncodingWithAllowedCharacters:[NSCharacterSet URLPathAllowedCharacterSet]];
    row.toggle = ^(BOOL on) {
        row.switchOn = on;
        if (on == ([m[@"enabled"] integerValue] == 1)) return;   // đúng trạng thái đang có: không gửi lại
        [vc busy:@"Đang lưu…"];
        [[GWA shared] send:@"PATCH" path:[NSString stringWithFormat:@"/gateway/models/%@", enc]
                      body:@{@"enabled": @(on)} done:^(id j, NSError *e) {
            [vc done];
            if (e != nil) { [vc report:e title:@"Không lưu được"]; }
            if (after) after();
        }];
    };
}

void gwModelTap(GWTableVC *vc, NSDictionary *m, void (^after)(void)) {
    NSString *mid = m[@"id"];
    if (mid.length == 0) return;
    NSString *enc = [mid stringByAddingPercentEncodingWithAllowedCharacters:[NSCharacterSet URLPathAllowedCharacterSet]];
    BOOL enabled = [m[@"enabled"] integerValue] == 1;
    NSString *note = m[@"last_test_message"];
    BOOL hasNote = [note isKindOfClass:[NSString class]] && note.length > 0;

    NSMutableArray<NSString *> *opts = [NSMutableArray array];
    [opts addObject:@"Kiểm tra model"];                          // 0
    [opts addObject:(enabled ? @"Tắt model" : @"Bật model")];     // 1
    if (hasNote) [opts addObject:@"Ghi chú lần kiểm"];            // 2
    NSInteger iNote = hasNote ? 2 : -1;
    NSInteger iDel = hasNote ? 3 : 2;
    [opts addObject:@"Xoá model"];

    [vc ask:mid options:opts run:^(NSInteger i) {
        if (i == 0) {
            [vc busy:@"Đang kiểm model…"];
            [[GWA shared] send:@"POST" path:@"/gateway/models/test" body:@{@"id": mid}
                          done:^(id j, NSError *e) {
                [vc done];
                if (e != nil) { [vc report:e title:@"Không kiểm được"]; return; }
                BOOL ok = [j[@"ok"] boolValue];
                NSString *msg = j[@"message"] ?: @"";
                [vc toast:(ok ? @"Model dùng được" : [NSString stringWithFormat:@"Model lỗi: %@", msg])];
                if (after) after();
            }];
        } else if (i == 1) {
            [vc busy:@"Đang lưu…"];
            [[GWA shared] send:@"PATCH" path:[NSString stringWithFormat:@"/gateway/models/%@", enc]
                          body:@{@"enabled": @(!enabled)} done:^(id j, NSError *e) {
                [vc done];
                if (e != nil) { [vc report:e title:@"Không lưu được"]; return; }
                if (after) after();
            }];
        } else if (i == iNote) {
            UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"Lần kiểm gần nhất"
                                                                      message:note
                                                               preferredStyle:UIAlertControllerStyleAlert];
            [ac addAction:[UIAlertAction actionWithTitle:@"Đóng" style:UIAlertActionStyleDefault handler:nil]];
            [vc presentViewController:ac animated:YES completion:nil];
        } else if (i == iDel) {
            UIAlertController *ac = [UIAlertController
                alertControllerWithTitle:@"Xoá model này?"
                                 message:@"Model sẽ mất khỏi danh sách cho tới lần dò sau."
                          preferredStyle:UIAlertControllerStyleAlert];
            [ac addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
            [ac addAction:[UIAlertAction actionWithTitle:@"Xoá" style:UIAlertActionStyleDestructive
                                                handler:^(UIAlertAction *a) {
                [vc busy:@"Đang xoá…"];
                [[GWA shared] send:@"DELETE" path:[NSString stringWithFormat:@"/gateway/models/%@", enc]
                              body:nil done:^(id j, NSError *e) {
                    [vc done];
                    if (e != nil) { [vc report:e title:@"Không xoá được"]; return; }
                    if (after) after();
                }];
            }]];
            [vc presentViewController:ac animated:YES completion:nil];
        }
    }];
}

#pragma mark - màn hình model

@interface ModelsVC ()
@property (nonatomic, assign) BOOL freeOnly;
@property (nonatomic, assign) BOOL hideUnverified;
@property (nonatomic, assign) BOOL gotSettings;
@end

@implementation ModelsVC

- (void)viewDidLoad {
    self.title = @"Model";
    [super viewDidLoad];
}

- (void)loadData {
    [self.sections removeAllObjects];
    GWSection *s = [self addSection:nil footer:nil];
    GWRow *w = [self addRow:s title:@"Đang đọc…" sub:nil];
    w.dotKind = 4;
    [self.tableView reloadData];

    GWA *api = [GWA shared];
    [api get:@"/gateway/settings" query:nil done:^(id j, NSError *e) {
        if (e == nil) {
            self.hideUnverified = [j[@"settings"][@"hide_unverified"] isEqual:@"1"];
            self.freeOnly = [j[@"settings"][@"free_models_only"] isEqual:@"1"];
            self.gotSettings = YES;
        }
        [api get:@"/gateway/models" query:nil done:^(id j2, NSError *e2) {
            [self buildWithModels:(e2 ? nil : j2[@"models"]) err:e2];
        }];
    }];
}

- (void)buildWithModels:(NSArray *)models err:(NSError *)e {
    [self.sections removeAllObjects];

    GWSection *pol = [self addSection:@"Chính sách model"
                               footer:@"Bật “chỉ dùng model miễn phí” thì cổng chỉ gọi các model được coi là miễn phí."];
    GWRow *free = [self addRow:pol title:@"Chỉ dùng model miễn phí"
                           sub:@"Tắt thì dùng được mọi model có key"];
    free.hasSwitch = YES;
    free.switchOn = self.freeOnly;
    free.toggle = ^(BOOL on) { [self setFreeOnly:on]; };

    GWRow *hide = [self addRow:pol title:@"Ẩn model chưa kiểm"
                           sub:(self.gotSettings ? @"Chỉ hiện model đã kiểm tốt" : @"(chưa đọc được cài đặt)")];
    hide.hasSwitch = YES;
    hide.switchOn = self.hideUnverified;
    hide.toggle = ^(BOOL on) { [self setHideUnverified:on]; };

    if (e != nil) {
        GWRow *bad = [self addRow:pol title:@"Không đọc được danh sách model" sub:e.localizedDescription];
        bad.dotKind = 2;
        [self.tableView reloadData];
        return;
    }

    NSMutableDictionary<NSString *, NSMutableArray *> *groups = [NSMutableDictionary dictionary];
    for (NSDictionary *m in models) {
        NSString *prov = m[@"provider"] ?: @"khác";
        if (groups[prov] == nil) groups[prov] = [NSMutableArray array];
        [groups[prov] addObject:m];
    }
    for (NSString *prov in [[groups allKeys] sortedArrayUsingSelector:@selector(compare:)]) {
        NSArray *list = groups[prov];
        NSInteger on = 0, ok = 0;
        for (NSDictionary *m in list) {
            if ([m[@"enabled"] integerValue] == 1) on++;
            if ([m[@"health"] isEqualToString:@"ok"]) ok++;
        }
        GWSection *sec = [self addSection:[NSString stringWithFormat:@"%@ (%lu model · %ld bật · %ld tốt)",
                                           gwProviderLabel(prov), (unsigned long)list.count, (long)on, (long)ok]
                                   footer:nil];
        for (NSDictionary *m in list) {
            GWRow *r = gwModelRow(m);
            [sec.rows addObject:r];
            gwModelSwitch(self, r, m, ^{ [self loadData]; });
            gwModelTap(self, m, ^{ [self loadData]; });
        }
    }
    if (models.count == 0) {
        GWSection *empty = [self addSection:nil footer:nil];
        [self addRow:empty title:@"Chưa có model nào" sub:@"Thêm key rồi cổng tự dò model."].dotKind = 4;
    }
    [self.tableView reloadData];
}

- (void)setFreeOnly:(BOOL)on {
    static int dbgFree = 0;
    if (dbgFree < 10) { dbgFree++; NSLog(@"DBG freeOnly %d st=%@", (int)on, gwStack()); }
    if (self.gotSettings && on == self.freeOnly) return;   // đúng giá trị đang lưu: không gửi lại
    [self busy:@"Đang lưu…"];
    [[GWA shared] send:@"POST" path:@"/gateway/models/policy" body:@{@"free_only": @(on)}
                  done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không lưu được"]; return; }
        self.freeOnly = on;
        [self toast:@"Đã lưu · cổng đang kiểm lại model"];
        [self.tableView reloadData];
    }];
}

- (void)setHideUnverified:(BOOL)on {
    static int dbgHide = 0;
    if (dbgHide < 10) { dbgHide++; NSLog(@"DBG hideUnv %d st=%@", (int)on, gwStack()); }
    if (self.gotSettings && on == self.hideUnverified) return;   // đúng giá trị đang lưu: không gửi lại
    [self busy:@"Đang lưu…"];
    [[GWA shared] send:@"POST" path:@"/gateway/settings" body:@{@"hide_unverified": (on ? @"1" : @"0")}
                  done:^(id j, NSError *e) {
        [self done];
        if (e != nil) { [self report:e title:@"Không lưu được"]; return; }
        self.hideUnverified = on;
        [self.tableView reloadData];
    }];
}

@end
